"""
synthesize_dub.py — IndexTTS2 batch dialogue synthesis (GPU)
=============================================================
Loads IndexTTS2 ONCE, iterates ALL speech lines in state_director.json
where audio_synthesis_status[track_mode] != "done", writes one
tts_audio/{track_mode}/raw_line_{NNN:03d}.wav per line.

Resumable: skips lines already marked done. Crash-safe: writes
state_director.json after EVERY line (atomic). Logs-and-continues past
per-line errors so a bad line doesn't abort the entire episode.

Args:
  --job_dir          jobs/{ep_folder}/
  --track_mode       standard | aave
  --characters_root  path to characters/ directory

Reads:  {job_dir}/state_director.json
Writes: {job_dir}/tts_audio/{track_mode}/raw_line_NNN.wav
        {job_dir}/status_synth_{track_mode}.json
        removes jobs/gpu.lock in finally
"""

import argparse
import glob
import json
import os
import sys
import time
import traceback
from pathlib import Path

GPU_LOCK = Path("jobs/gpu.lock")

EMOTION_FALLBACK_ORDER = [
    "neutral", "cheerful", "angry", "sad",
    "whisper", "exhausted", "excited", "fearful",
]


def write_status(job_dir: Path, track: str, payload: dict):
    p = job_dir / f"status_synth_{track}.json"
    cur: dict = {}
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
    p = job_dir / f"status_synth_{track}.json"
    logs: list = []
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


def find_ref_wav(char_dir: Path, emotion: str) -> Path | None:
    """
    Lookup priority: ref_{emotion}.wav → ref_neutral.wav → first ref_*.wav
    Never crash on missing files — always return something or None.
    """
    target = char_dir / f"ref_{emotion}.wav"
    if target.exists():
        return target
    neutral = char_dir / "ref_neutral.wav"
    if neutral.exists():
        return neutral
    candidates = sorted(char_dir.glob("ref_*.wav"))
    return candidates[0] if candidates else None


def locate_char_dir(characters_root: Path, show_name: str, character: str) -> Path | None:
    """Check show dir (exact then slug), then global_roster fallback."""
    import re
    # Try exact show name
    show_dir = characters_root / "shows" / show_name / character
    if show_dir.exists():
        return show_dir
    # Try slugified show name (lowercase, spaces→underscores)
    slug = re.sub(r"[^a-z0-9]+", "_", show_name.lower()).strip("_")
    slug_dir = characters_root / "shows" / slug / character
    if slug_dir.exists():
        return slug_dir
    global_dir = characters_root / "global_roster" / character
    if global_dir.exists():
        return global_dir
    # Try generic fallbacks
    for generic in ["generic_male_01", "generic_female_01"]:
        g = characters_root / "global_roster" / generic
        if g.exists():
            return g
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job_dir",          required=True)
    ap.add_argument("--track_mode",       required=True, choices=["standard", "aave"])
    ap.add_argument("--characters_root",  default="characters")
    ap.add_argument("--line_start",       type=int, default=None, help="First line_index to process (inclusive)")
    ap.add_argument("--line_end",         type=int, default=None, help="Last line_index to process (inclusive)")
    ap.add_argument("--worker_id",        type=int, default=0,    help="Worker index (for status file naming in parallel mode)")
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    track = args.track_mode
    chars_root = Path(args.characters_root)
    line_start = args.line_start
    line_end = args.line_end
    worker_id = args.worker_id

    # In parallel/worker mode write per-worker status; otherwise use the standard file
    status_suffix = f"_w{worker_id}" if worker_id > 0 else ""

    write_status(job_dir, track, {
        "stage": f"synth_{track}", "status": "processing",
        "progress": 0, "error": None, "logs": [],
        **({"worker_id": worker_id, "line_start": line_start, "line_end": line_end} if worker_id > 0 else {}),
    })

    # Acquire GPU lock
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if GPU_LOCK.exists():
        holder = GPU_LOCK.read_text(encoding="utf-8").strip()
        print(f"[synth] GPU busy: {holder}", file=sys.stderr, flush=True)
        sys.exit(2)

    ep = job_dir.name
    GPU_LOCK.write_text(f"synth_{track}:{ep}", encoding="utf-8")

    model = None
    try:
        state_path = job_dir / "state_director.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        show_name = state.get("show_name", "")
        lines = state.get("lines", [])

        todo = [
            ln for ln in lines
            if ln.get("type") == "speech"
            and (
                not isinstance(ln.get("audio_synthesis_status"), dict)
                or ln["audio_synthesis_status"].get(track) != "done"
            )
            and (line_start is None or ln.get("line_index", 0) >= line_start)
            and (line_end   is None or ln.get("line_index", 0) <= line_end)
        ]
        total = len(todo)
        log(job_dir, track, f"{total} lines to synthesize for track={track}")

        if total == 0:
            write_status(job_dir, track, {
                "status": "done", "progress": 100,
                "result": {"synthesized": 0, "skipped": 0, "errors": 0},
            })
            return

        # Load IndexTTS2 once
        log(job_dir, track, "Loading IndexTTS2 model…")
        try:
            from indextts.infer import IndexTTS
            model = IndexTTS(model_dir="C:/Users/shani/OneDrive/Desktop/IndexTTS2/checkpoints", cfg_path="C:/Users/shani/OneDrive/Desktop/IndexTTS2/checkpoints/config.yaml")
        except Exception as e:
            raise ImportError(f"IndexTTS2 not available: {e}")

        log(job_dir, track, "IndexTTS2 loaded")

        out_dir = job_dir / "tts_audio" / track
        out_dir.mkdir(parents=True, exist_ok=True)

        synthesized = skipped = errors = 0

        for i, ln in enumerate(todo):
            idx = ln["line_index"]
            out_path = out_dir / f"raw_line_{idx:03d}.wav"

            # Skip if output already exists (extra safety for resumability)
            if out_path.exists():
                ln.setdefault("audio_synthesis_status", {})[track] = "done"
                ln.setdefault("raw_wav", {})[track] = str(out_path)
                skipped += 1
                save_state(state_path, state)
                continue

            try:
                character = ln.get("character", "").lower().replace(" ", "_")
                emotion = ln.get("detected_emotion", "neutral")
                text_key = f"text_{track}"
                text = ln.get(text_key) or ln.get("text_standard") or ln.get("source_text", "")

                if not text.strip():
                    raise ValueError(f"Empty text for line {idx} track={track}")

                char_dir = locate_char_dir(chars_root, show_name, character)
                if char_dir is None:
                    raise FileNotFoundError(
                        f"No character dir for '{character}' in show '{show_name}'"
                    )

                ref_wav = find_ref_wav(char_dir, emotion)
                if ref_wav is None:
                    raise FileNotFoundError(
                        f"No ref wav found for character='{character}' emotion='{emotion}'"
                    )

                log(job_dir, track,
                    f"Line {idx:03d}: {character}/{emotion} → '{text[:60]}'")

                # IndexTTS2 inference — args list, no shell
                model.infer(
                    audio_prompt=str(ref_wav),
                    text=text,
                    output_path=str(out_path),
                )

                ln.setdefault("audio_synthesis_status", {})[track] = "done"
                ln.setdefault("raw_wav", {})[track] = str(out_path)
                synthesized += 1

            except Exception as e:
                errors += 1
                ln.setdefault("audio_synthesis_status", {})[track] = "error"
                ln["error_msg"] = f"synth {track}: {e}"
                log(job_dir, track, f"Line {idx:03d}: ERROR — {e}")

            # Atomic write-through after EVERY line (crash-safe, resumable)
            save_state(state_path, state)
            write_status(job_dir, track, {
                "progress": int(100 * (i + 1) / total),
                "result": {"synthesized": synthesized, "skipped": skipped, "errors": errors},
            })

        final_status = "done" if errors < total else "error"
        write_status(job_dir, track, {
            "status": final_status,
            "progress": 100,
            "result": {"synthesized": synthesized, "skipped": skipped, "errors": errors},
            "error": None if final_status == "done" else f"{errors} lines failed",
        })
        log(job_dir, track,
            f"Synthesis complete — done:{synthesized} skipped:{skipped} errors:{errors}")

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, track, {"status": "error", "error": str(e)})
        sys.exit(1)
    finally:
        if model is not None:
            try:
                del model
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
        if GPU_LOCK.exists():
            try:
                GPU_LOCK.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
