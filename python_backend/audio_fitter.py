"""
audio_fitter.py — Yanflix Stage: Audio Fitting + QC Scoring (CPU only)
=======================================================================
For each synthesized line:
  1. Compute pitch-preserving time-stretch to fit the line into its timeline slot.
  2. Score the fitted WAV with NISQA MOS (or SNR/flatness heuristic fallback).
  3. Flag lines below the quality threshold in state_director.json so the UI
     can highlight them for manual regeneration before the final render.

Stretch:
    rate   = current_duration / target_duration
    target = end - start (from state_director.json)
    rate is CLAMPED to [min_rate, max_rate] (default 0.7–1.3). When clamped,
    a warning is logged and the line is padded/trimmed to the exact slot length.

QC:
    After stretching, each fit WAV is scored. Lines below --mos_flag_threshold
    (default 3.2) are marked synthesis_quality[track] = "flagged" in
    state_director.json. Lines above are marked "passed". The status file
    reports flagged_count so the n8n render gate can pause for review.

    synthesis_quality values: "passed" | "flagged" | "error" | "pending"

Engine priority: pyrubberband → librosa.effects.time_stretch fallback.
Resumable: skips lines whose audio_fit_status[track_mode] == "done".
Write-through: state_director.json updated atomically after EVERY line.

Usage:
  conda run -n dubbing python python_backend/audio_fitter.py \
      --job_dir ./jobs/smoking_behind_the_supermarket_with_you_s01e01 \
      --track_mode standard \
      --min_rate 0.7 --max_rate 1.3 --mos_flag_threshold 3.2

Reads:  {job_dir}/state_director.json
Writes: {job_dir}/tts_audio/{track_mode}/fit_line_NNN.wav
        {job_dir}/status_fit_{track_mode}.json  (includes qc summary)
"""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

try:
    import soundfile as sf
except ImportError:
    print("FATAL: soundfile is required (pip install soundfile)", file=sys.stderr)
    sys.exit(1)

try:
    import librosa
except ImportError:
    print("FATAL: librosa is required (pip install librosa)", file=sys.stderr)
    sys.exit(1)

try:
    import pyrubberband as pyrb
    _HAS_RB = True
except ImportError:
    pyrb: Any = None
    _HAS_RB = False

# NISQA — same graceful fallback pattern as harvest_voices.py
nisqaModel: Any = None
nisqa: Any = None
_NISQA_MODE = None
try:
    from nisqa.NISQA_model import nisqaModel  # type: ignore[assignment]
    _NISQA_MODE = "repo"
except Exception:
    try:
        import nisqa  # type: ignore[assignment]
        _NISQA_MODE = "pip"
    except Exception:
        _NISQA_MODE = None


# ---------------- status helpers ----------------
def status_path(job_dir: Path, track: str) -> Path:
    return job_dir / f"status_fit_{track}.json"


def write_status(job_dir: Path, track: str, payload: dict):
    p = status_path(job_dir, track)
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


def log(job_dir: Path, track: str, msg: str):
    print(msg, flush=True)
    p = status_path(job_dir, track)
    logs = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, track, {"logs": logs[-300:]})


def save_state(state_path: Path, state: dict):
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)


# ---------------- quality scorer ----------------
def heuristic_mos(y: np.ndarray, sr: int) -> float:
    """SNR/clipping/flatness pseudo-MOS. Ranks clips correctly; absolute values approximate."""
    if len(y) < sr // 4:
        return 1.0
    frame, hop = 2048, 512
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
    return float(1.0 + 4.0 * (0.5 * snr_score + 0.2 * clip_score + 0.3 * flat_score))


class QualityScorer:
    def __init__(self, job_dir: Path, track: str):
        self.mode = "heuristic"
        self._model: Any = None
        if _NISQA_MODE == "repo":
            try:
                args = {
                    "mode": "predict_file",
                    "pretrained_model": str(Path(__file__).parent / "weights" / "nisqa.tar"),
                    "ms_channel": None,
                }
                self._model = nisqaModel(args)
                self.mode = "nisqa"
            except Exception as e:
                log(job_dir, track, f"NISQA unavailable ({e}); using heuristic MOS for QC")
        elif _NISQA_MODE == "pip":
            self.mode = "nisqa_pip"
        log(job_dir, track, f"QC scorer: {self.mode}")

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
                return float(nisqa.predict(str(wav_path))["mos"])
            except Exception:
                return heuristic_mos(y, sr)
        return heuristic_mos(y, sr)


# ---------------- stretching ----------------
def stretch(y: np.ndarray, sr: int, rate: float, engine: str) -> np.ndarray:
    """rate > 1.0 → faster/shorter; rate < 1.0 → slower/longer. Pitch preserved."""
    if abs(rate - 1.0) < 0.02:
        return y
    if engine == "rubberband":
        return pyrb.time_stretch(y, sr, rate)
    return librosa.effects.time_stretch(y, rate=rate)


def fit_line(raw_path: Path, out_path: Path, target_dur: float,
             min_rate: float, max_rate: float, engine: str,
             pad_to_target: bool = True):
    """Returns (applied_rate, clamped: bool, final_dur)."""
    y, sr = librosa.load(str(raw_path), sr=None, mono=True)
    cur_dur = len(y) / sr
    if target_dur <= 0.05:
        raise ValueError(f"target duration {target_dur:.3f}s is invalid")

    desired_rate = cur_dur / target_dur
    rate = float(np.clip(desired_rate, min_rate, max_rate))
    clamped = abs(rate - desired_rate) > 1e-6

    y2 = stretch(y, sr, rate, engine)
    final_dur = len(y2) / sr

    if pad_to_target:
        target_samples = int(round(target_dur * sr))
        if len(y2) > target_samples:
            y2 = y2[:target_samples]
            fade = min(int(0.03 * sr), len(y2))
            if fade > 0:
                y2[-fade:] *= np.linspace(1.0, 0.0, fade)
        elif len(y2) < target_samples:
            y2 = np.concatenate([y2, np.zeros(target_samples - len(y2), dtype=y2.dtype)])
        final_dur = len(y2) / sr

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), y2, sr)
    return rate, clamped, final_dur


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job_dir", required=True)
    ap.add_argument("--track_mode", required=True, choices=["standard", "aave"])
    ap.add_argument("--min_rate", type=float, default=0.7)
    ap.add_argument("--max_rate", type=float, default=1.3)
    ap.add_argument("--mos_flag_threshold", type=float, default=3.2,
                    help="Lines with MOS below this are flagged for manual review. "
                         "Default 3.2 (on a 1–5 scale). Set to 0 to disable flagging.")
    ap.add_argument("--no_pad", action="store_true")
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    track = args.track_mode
    write_status(job_dir, track, {"stage": f"fit_{track}", "status": "processing",
                                  "progress": 0, "error": None, "logs": [],
                                  "qc_threshold": args.mos_flag_threshold})

    engine = "rubberband" if _HAS_RB else "librosa"
    if engine == "librosa":
        log(job_dir, track, "pyrubberband not found — using librosa fallback "
                            "(install rubberband-cli + pyrubberband for best quality)")
    else:
        log(job_dir, track, "Stretch engine: rubberband phase vocoder")

    scorer = QualityScorer(job_dir, track)

    try:
        state_path = job_dir / "state_director.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        lines = state.get("lines", [])

        todo = []
        for ln in lines:
            if ln.get("type") != "speech":
                continue
            synth = ln.get("audio_synthesis_status", {})
            fit = ln.get("audio_fit_status", {})
            if isinstance(synth, dict) and synth.get(track) != "done":
                continue
            if isinstance(fit, dict) and fit.get(track) == "done":
                continue
            todo.append(ln)

        total = len(todo)
        log(job_dir, track, f"{total} lines to fit+score (threshold MOS {args.mos_flag_threshold})")
        if total == 0:
            write_status(job_dir, track, {
                "status": "done", "progress": 100,
                "result": {"fitted": 0, "clamped": 0, "errors": 0,
                           "qc_passed": 0, "qc_flagged": 0, "flagged_lines": []}
            })
            return

        fitted = clamped_count = errors = 0
        qc_passed = qc_flagged = 0
        flagged_lines = []  # list of line_index values for the UI to highlight

        for i, ln in enumerate(todo):
            idx = ln["line_index"]
            try:
                raw = ln.get("raw_wav", {}).get(track, "")
                raw_path = Path(raw) if raw else Path("")
                fallback = job_dir / "tts_audio" / track / f"raw_line_{idx:03d}.wav"
                if (not raw or not raw_path.exists()) and fallback.exists():
                    raw_path = fallback
                if not raw_path.exists():
                    raise FileNotFoundError(f"raw wav missing for line {idx}: {raw_path}")

                target_dur = float(ln["end"]) - float(ln["start"])
                out_path = job_dir / "tts_audio" / track / f"fit_line_{idx:03d}.wav"

                rate, was_clamped, final_dur = fit_line(
                    raw_path, out_path, target_dur,
                    args.min_rate, args.max_rate, engine,
                    pad_to_target=not args.no_pad,
                )

                if was_clamped:
                    clamped_count += 1
                    log(job_dir, track,
                        f"line {idx:03d}: CLAMPED rate→{rate:.3f} "
                        f"slot={target_dur:.2f}s final={final_dur:.2f}s")

                # ── QC scoring on the fitted output ──────────────────────────
                mos = scorer.score(out_path)
                qc_result = "flagged" if mos < args.mos_flag_threshold else "passed"
                if qc_result == "flagged":
                    qc_flagged += 1
                    flagged_lines.append(idx)
                    log(job_dir, track,
                        f"line {idx:03d}: ⚠ QC FLAGGED  MOS={mos:.2f} "
                        f"(threshold {args.mos_flag_threshold}) — "
                        f"'{ln.get('text_' + track, '')[:60]}'")
                else:
                    qc_passed += 1

                # ── write results into state_director.json ───────────────────
                ln.setdefault("audio_fit_status", {})[track] = "done"
                ln.setdefault("fit_wav", {})[track] = str(out_path)
                ln["fit_rate_" + track] = round(rate, 4)
                ln.setdefault("synthesis_quality", {})[track] = qc_result
                ln.setdefault("mos_score", {})[track] = round(mos, 3)
                fitted += 1

            except Exception as e:
                errors += 1
                ln.setdefault("audio_fit_status", {})[track] = "error"
                ln.setdefault("synthesis_quality", {})[track] = "error"
                ln["error_msg"] = f"fit: {e}"
                log(job_dir, track, f"line {idx:03d}: ERROR {e}")

            # write-through after EVERY line (crash-safe, resumable)
            save_state(state_path, state)
            write_status(job_dir, track, {"progress": int(100 * (i + 1) / total)})

        result = {
            "fitted": fitted,
            "clamped": clamped_count,
            "errors": errors,
            "qc_scorer": scorer.mode,
            "qc_threshold": args.mos_flag_threshold,
            "qc_passed": qc_passed,
            "qc_flagged": qc_flagged,
            "flagged_lines": flagged_lines,   # UI reads this to highlight bad lines
        }

        # Status is "done" even if lines were flagged — flagging is a review
        # signal, not a failure. The n8n render gate checks qc_flagged count.
        final_status = "done" if fitted > 0 or errors == 0 else "error"
        write_status(job_dir, track, {
            "status": final_status,
            "progress": 100,
            "result": result,
            "error": None if final_status == "done" else f"{errors} lines failed to fit"
        })
        log(job_dir, track,
            f"Fit+QC complete — passed: {qc_passed}, flagged: {qc_flagged}, "
            f"errors: {errors}, clamped: {clamped_count}")

        if qc_flagged > 0:
            log(job_dir, track,
                f"⚠  {qc_flagged} lines flagged for review. "
                f"Flagged line indexes: {flagged_lines}")

        if errors > 0 and fitted == 0:
            sys.exit(1)

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, track, {"status": "error", "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
