"""
build_emotion_bank.py — Local IndexTTS2 emotion bank builder
============================================================
For each character in characters/shows/{show}/: uses the best harvested
seed WAV as a voice reference and generates ref_{emotion}.wav files via
IndexTTS2. This is the local alternative to ElevenLabs voice cloning.

Args:
  --show             show slug (e.g. smoking_behind_the_supermarket_with_you)
  --characters_root  path to characters/ directory
  --job_dir          jobs/{ep_folder}/ (for status file + GPU lock)

Writes: characters/shows/{show}/{char}/ref_{emotion}.wav  (8 files per char)
        characters/shows/{show}/{char}/profile.json       (bank_complete, bank_local)
        {job_dir}/status_clone.json                       (progress tracking)
"""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

GPU_LOCK = Path("jobs/gpu.lock")

EMOTION_PROMPTS = [
    ("neutral",   "I never thought this day would actually come, but here we are. After everything we have been through, this changes absolutely everything."),
    ("cheerful",  "Oh, this is wonderful news! Everything came together better than I ever dared to hope."),
    ("angry",     "I told you this would happen. You never listen, and now look at the mess you've made!"),
    ("sad",       "I just… I don't know what to say. Some things can never really be undone, can they."),
    ("whisper",   "Don't make a sound. They're right outside — if they hear us it's over."),
    ("exhausted", "I've been awake for three days straight. I can't keep doing this much longer."),
    ("excited",   "Did you see that? It actually worked! We did it — I can't believe we actually did it!"),
    ("fearful",   "Something's wrong. I can feel it. We shouldn't be here — we need to leave right now."),
]


def write_status(job_dir: Path, payload: dict):
    p = job_dir / "status_clone.json"
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


def log(job_dir: Path, msg: str):
    print(msg, flush=True)
    p = job_dir / "status_clone.json"
    logs: list = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, {"logs": logs[-300:]})


def atomic_write(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show",            required=True)
    ap.add_argument("--characters_root", default="./characters")
    ap.add_argument("--job_dir",         required=True)
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    chars_root = Path(args.characters_root)
    show_dir = chars_root / "shows" / args.show

    write_status(job_dir, {
        "stage": "clone", "status": "processing", "progress": 0,
        "step": "loading IndexTTS2", "logs": [], "error": None,
        "method": "indextts2_local",
    })

    if GPU_LOCK.exists():
        holder = GPU_LOCK.read_text(encoding="utf-8").strip()
        write_status(job_dir, {"status": "error", "error": f"GPU locked by: {holder}"})
        sys.exit(2)

    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    GPU_LOCK.write_text(f"clone_bank:{job_dir.name}", encoding="utf-8")

    model = None
    try:
        log(job_dir, "Loading IndexTTS2 model…")
        from indextts.infer import IndexTTS
        model = IndexTTS(model_dir="C:/Users/shani/OneDrive/Desktop/IndexTTS2/checkpoints", cfg_path="C:/Users/shani/OneDrive/Desktop/IndexTTS2/checkpoints/config.yaml")
        log(job_dir, "IndexTTS2 loaded")

        char_dirs = sorted([d for d in show_dir.iterdir() if d.is_dir()])
        if not char_dirs:
            raise RuntimeError(f"No character directories found in {show_dir}")

        results: dict = {}

        for i, char_dir in enumerate(char_dirs):
            char_name = char_dir.name
            pct = int(100 * i / len(char_dirs))
            write_status(job_dir, {"progress": pct, "step": f"{char_name} ({i+1}/{len(char_dirs)})"})
            log(job_dir, f"── {char_name}")

            # Skip if already fully banked
            prof_path = char_dir / "profile.json"
            profile: dict = {}
            if prof_path.exists():
                try:
                    profile = json.loads(prof_path.read_text(encoding="utf-8"))
                except Exception:
                    profile = {}
            if profile.get("bank_complete") and profile.get("bank_local"):
                log(job_dir, "   skipped — bank already complete")
                results[char_name] = {"skipped": True}
                continue

            # Find best seed
            seeds_dir = char_dir / "seeds"
            seeds = sorted(seeds_dir.glob("seed_*.wav")) if seeds_dir.exists() else []
            if not seeds:
                log(job_dir, "   SKIPPED — no seeds")
                results[char_name] = {"error": "no seeds"}
                continue

            ref_seed = seeds[0]  # seed_00 = highest MOS (harvest_voices sorts descending)
            log(job_dir, f"   reference: {ref_seed.name}")

            bank_meta: dict = {}
            bank_errors = 0

            for emotion, text in EMOTION_PROMPTS:
                out_path = char_dir / f"ref_{emotion}.wav"
                if out_path.exists():
                    bank_meta[emotion] = str(out_path)
                    continue

                log(job_dir, f"   gen ref_{emotion}.wav")
                try:
                    model.infer(
                        audio_prompt=str(ref_seed),
                        text=text,
                        output_path=str(out_path),
                    )
                    bank_meta[emotion] = str(out_path)
                except Exception as e:
                    bank_errors += 1
                    log(job_dir, f"   ERROR ref_{emotion}: {e}")

            bank_complete = len(bank_meta) == len(EMOTION_PROMPTS)
            profile.update({
                "bank": bank_meta,
                "bank_complete": bank_complete,
                "bank_local": True,
                "bank_errors": bank_errors,
                "cloned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "method": "indextts2_local",
            })
            atomic_write(prof_path, profile)

            log(job_dir, f"   done — {len(bank_meta)}/8 emotions, {bank_errors} errors")
            results[char_name] = {"bank_complete": bank_complete, "bank_errors": bank_errors, "emotions": len(bank_meta)}

        write_status(job_dir, {
            "status": "done", "progress": 100,
            "step": "complete", "result": results,
        })
        log(job_dir, f"Bank build complete: {len(results)} characters")

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, {"status": "error", "error": str(e)})
        sys.exit(1)
    finally:
        if GPU_LOCK.exists():
            try:
                GPU_LOCK.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
