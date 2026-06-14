"""
harvest_voices.py — Yanflix Stage: Voice Seed Harvesting
=========================================================
Per character: rank that character's line clips by quality (NISQA MOS if
available, SNR/energy heuristic fallback), keep the top N clean clips >= MIN_SEC,
copy them to characters/shows/{show}/{char}/seeds/, compute a speaker embedding
(resemblyzer if available, MFCC-stat fallback), and upsert into ChromaDB
(HTTP, port 8000) so future episodes skip ElevenLabs entirely.

CPU-first. Safe to run while no GPU job holds jobs/gpu.lock (embedding models
are tiny; NISQA runs on CPU here by design).

Usage:
  conda run -n dubbing python python_backend/harvest_voices.py \
      --job_dir ./jobs/smoking_behind_the_supermarket_with_you_s01e01 \
      --show smoking_behind_the_supermarket_with_you \
      --characters_root ./characters \
      --chroma_host localhost --chroma_port 8000 \
      --top_n 5 --min_sec 2.0 --min_mos 3.0

Reads:  {job_dir}/state_director.json  (lines[] with character + clip_path)
Writes: {job_dir}/status_harvest.json  (processing/done/error + progress)
        characters/shows/{show}/{char}/seeds/seed_NN.wav
        characters/shows/{show}/{char}/profile.json (merged, not overwritten)
        ChromaDB collection "yanflix_voices"
"""

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from typing import Any

import numpy as np

# ---------- optional deps with graceful fallbacks ----------
try:
    import librosa
except ImportError:
    print("FATAL: librosa is required (pip install librosa)", file=sys.stderr)
    sys.exit(1)

try:
    import resemblyzer  # noqa: F401
    _HAS_RESEMBLYZER = True
except ImportError:
    _HAS_RESEMBLYZER = False

try:
    import chromadb  # noqa: F401
    _HAS_CHROMA = True
except ImportError:
    _HAS_CHROMA = False

# NISQA: package APIs vary by install; we try, then fall back to a heuristic.
_NISQA_MODE: str | None = None
try:
    import nisqa.NISQA_model  # noqa: F401
    _NISQA_MODE = "repo"
except Exception:
    try:
        import nisqa  # noqa: F401
        _NISQA_MODE = "pip"
    except Exception:
        _NISQA_MODE = None


# ---------------- status helpers ----------------
def write_status(job_dir: Path, payload: dict):
    p = job_dir / "status_harvest.json"
    cur = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(payload)
    cur["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def log(job_dir: Path, msg: str):
    print(msg, flush=True)
    p = job_dir / "status_harvest.json"
    logs = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, {"logs": logs[-200:]})


# ---------------- quality scoring ----------------
def heuristic_mos(y: np.ndarray, sr: int) -> float:
    """
    Cheap stand-in for NISQA when it isn't importable.
    Combines an SNR estimate, clipping check, and spectral flatness into a
    pseudo-MOS on roughly the same 1-5 scale. Good enough to RANK clips of the
    same speaker; absolute values are approximate.
    """
    if len(y) < sr // 4:
        return 1.0
    frame = 2048
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    if rms.max() <= 0:
        return 1.0
    lo = np.percentile(rms, 10) + 1e-9
    hi = np.percentile(rms, 90)
    snr_db = 20.0 * np.log10(hi / lo)
    snr_score = np.clip((snr_db - 5.0) / 35.0, 0.0, 1.0)
    clip_ratio = float(np.mean(np.abs(y) > 0.985))
    clip_score = 1.0 - np.clip(clip_ratio * 50.0, 0.0, 1.0)
    flat = float(np.mean(librosa.feature.spectral_flatness(y=y)))
    flat_score = 1.0 - np.clip((flat - 0.1) / 0.4, 0.0, 1.0)
    score01 = 0.5 * snr_score + 0.2 * clip_score + 0.3 * flat_score
    return float(1.0 + 4.0 * score01)


class QualityScorer:
    _model: Any

    def __init__(self, job_dir: Path):
        self.mode = "heuristic"
        self._model = None
        if _NISQA_MODE == "repo":
            try:
                from nisqa.NISQA_model import nisqaModel  # type: ignore[import-untyped]
                model_args = {
                    "mode": "predict_file",
                    "pretrained_model": str(Path(__file__).parent / "weights" / "nisqa.tar"),
                    "ms_channel": None,
                }
                self._model = nisqaModel(model_args)
                self.mode = "nisqa"
            except Exception as e:
                log(job_dir, f"NISQA repo-mode unavailable ({e}); using heuristic MOS")
        elif _NISQA_MODE == "pip":
            self.mode = "nisqa_pip"
        if self.mode == "heuristic":
            log(job_dir, "Quality scorer: SNR/flatness heuristic (NISQA not importable)")
        else:
            log(job_dir, f"Quality scorer: {self.mode}")

    def score(self, wav_path: Path) -> float:
        y, sr = librosa.load(str(wav_path), sr=16000, mono=True)
        if self.mode == "nisqa":
            try:
                self._model.args["deg"] = str(wav_path)
                df = self._model.predict()
                return float(df["mos_pred"].iloc[0])
            except Exception:
                return heuristic_mos(y, sr)
        if self.mode == "nisqa_pip":
            try:
                import nisqa as _nisqa  # type: ignore[import-untyped]
                return float(_nisqa.predict(str(wav_path))["mos"])
            except Exception:
                return heuristic_mos(y, sr)
        return heuristic_mos(y, sr)


# ---------------- embeddings ----------------
class Embedder:
    encoder: Any

    def __init__(self, job_dir: Path):
        if _HAS_RESEMBLYZER:
            from resemblyzer import VoiceEncoder  # type: ignore[import-untyped]
            self.encoder = VoiceEncoder("cpu")
            self.mode = "resemblyzer"
        else:
            self.encoder = None
            self.mode = "mfcc"
            log(job_dir, "resemblyzer not installed; using MFCC-stat embedding "
                         "(pip install resemblyzer for better cross-episode matching)")

    def embed(self, wav_paths) -> list:
        if self.mode == "resemblyzer":
            from resemblyzer import preprocess_wav  # type: ignore[import-untyped]
            wavs = [preprocess_wav(str(p)) for p in wav_paths]
            embs = [self.encoder.embed_utterance(w) for w in wavs]
            return np.mean(np.stack(embs), axis=0).tolist()
        feats = []
        for p in wav_paths:
            y, sr = librosa.load(str(p), sr=16000, mono=True)
            m = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=48)
            feats.append(np.concatenate([m.mean(axis=1), m.std(axis=1)]))
        v = np.mean(np.stack(feats), axis=0)
        v = v / (np.linalg.norm(v) + 1e-9)
        out = np.zeros(192, dtype=np.float32)
        out[: min(192, len(v))] = v[:192]
        return out.tolist()


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job_dir", required=True)
    ap.add_argument("--show", required=True)
    ap.add_argument("--characters_root", default="./characters")
    ap.add_argument("--chroma_host", default="localhost")
    ap.add_argument("--chroma_port", type=int, default=8000)
    ap.add_argument("--top_n", type=int, default=5)
    ap.add_argument("--min_sec", type=float, default=2.0)
    ap.add_argument("--min_mos", type=float, default=3.0,
                    help="Quality gate. Clips below this are rejected; if a character "
                         "has zero passing clips, its best clip is kept with a warning.")
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_status(job_dir, {"stage": "harvest", "status": "processing", "progress": 0,
                           "error": None, "logs": []})
    try:
        state_path = job_dir / "state_director.json"
        if not state_path.exists():
            raise FileNotFoundError(f"{state_path} not found — run script-director first")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        lines = state.get("lines", [])

        by_char = {}
        for ln in lines:
            if ln.get("type") != "speech":
                continue
            ch = ln.get("character")
            clip = ln.get("clip_path")
            if not ch or not clip:
                continue
            cp = Path(clip)
            if not cp.is_absolute():
                cp = Path.cwd() / cp
            if cp.exists():
                by_char.setdefault(ch, []).append(cp)

        if not by_char:
            raise RuntimeError("No per-line clips found. Run segment_lines first "
                               "(state_director lines need clip_path).")

        scorer = QualityScorer(job_dir)
        embedder = Embedder(job_dir)

        chroma_coll: Any = None
        if _HAS_CHROMA:
            try:
                import chromadb as _chromadb  # type: ignore[import-untyped]
                client = _chromadb.HttpClient(host=args.chroma_host, port=args.chroma_port)
                chroma_coll = client.get_or_create_collection("yanflix_voices")
                log(job_dir, f"ChromaDB connected at {args.chroma_host}:{args.chroma_port}")
            except Exception as e:
                log(job_dir, f"WARNING: ChromaDB unreachable ({e}) — skipping registry upsert")
        else:
            log(job_dir, "WARNING: chromadb not installed — skipping registry upsert")

        results = {}
        chars = sorted(by_char.keys())
        for i, ch in enumerate(chars):
            log(job_dir, f"── {ch}: scoring {len(by_char[ch])} clips")
            scored = []
            for cp in by_char[ch]:
                try:
                    dur = librosa.get_duration(path=str(cp))
                except Exception:
                    continue
                if dur < args.min_sec:
                    continue
                mos = scorer.score(cp)
                scored.append((mos, dur, cp))
            scored.sort(key=lambda t: t[0], reverse=True)

            passing = [s for s in scored if s[0] >= args.min_mos]
            if not passing and scored:
                log(job_dir, f"   WARNING: no clip passed MOS>={args.min_mos}; "
                             f"keeping best ({scored[0][0]:.2f}) anyway")
                passing = scored[:1]
            chosen = passing[: args.top_n]
            if not chosen:
                log(job_dir, f"   SKIPPED: no usable clips >= {args.min_sec}s")
                continue

            char_dir = Path(args.characters_root) / "shows" / args.show / ch
            seeds_dir = char_dir / "seeds"
            seeds_dir.mkdir(parents=True, exist_ok=True)
            seed_paths, seed_meta = [], []
            for n, (mos, dur, cp) in enumerate(chosen):
                dst = seeds_dir / f"seed_{n:02d}.wav"
                shutil.copy2(cp, dst)
                seed_paths.append(dst)
                seed_meta.append({"file": dst.name, "mos": round(mos, 3),
                                  "duration": round(dur, 2), "source": str(cp)})
                log(job_dir, f"   seed_{n:02d}.wav  MOS={mos:.2f}  {dur:.1f}s")

            embedding = embedder.embed(seed_paths)
            chroma_id = f"{args.show}::{ch}"
            if chroma_coll is not None:
                chroma_coll.upsert(
                    ids=[chroma_id],
                    embeddings=[embedding],
                    metadatas=[{"show": args.show, "character": ch,
                                "seed_count": len(seed_paths),
                                "embed_mode": embedder.mode,
                                "bank_complete": False}],
                )

            prof_path = char_dir / "profile.json"
            prof = {}
            if prof_path.exists():
                try:
                    prof = json.loads(prof_path.read_text(encoding="utf-8"))
                except Exception:
                    prof = {}
            prof.update({"character": ch, "show": args.show, "chroma_id": chroma_id,
                         "seeds": seed_meta, "scorer": scorer.mode,
                         "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            prof.setdefault("bank_complete", False)
            prof_path.write_text(json.dumps(prof, indent=2), encoding="utf-8")

            results[ch] = {"seeds": len(seed_paths), "best_mos": round(chosen[0][0], 2),
                           "chroma_id": chroma_id}
            write_status(job_dir, {"progress": int(100 * (i + 1) / len(chars))})

        state.setdefault("characters", {})
        for ch, r in results.items():
            entry = state["characters"].setdefault(ch, {})
            entry.update({"chroma_id": r["chroma_id"],
                          "bank_dir": str(Path(args.characters_root) / "shows" / args.show / ch),
                          "seeds_harvested": r["seeds"]})
            entry.setdefault("bank_complete", False)
        tmp = state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, state_path)

        write_status(job_dir, {"status": "done", "progress": 100, "result": results})
        log(job_dir, f"Harvest complete: {len(results)} characters")

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, {"status": "error", "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
