"""
isolate.py — Demucs vocal isolation (GPU)
=========================================
Args:
  --video      path to source MP4 in workspace/0_raw_videos/
  --ep         ep_folder slug (e.g. smoking_supermarket_s01e01)
  --output_dir workspace/2_isolated/ root  (parent; script appends ep sub-dir)

Writes:
  workspace/2_isolated/{ep}/vocals.wav        ← stable path
  workspace/2_isolated/{ep}/no_vocals.wav     ← stable path (instrumental)
  jobs/{ep}/status_isolate.json
  jobs/gpu.lock  (removed in finally)

Demucs nests output: output_dir/{ep}/htdemucs/{basename}/vocals.wav
This worker copies stems to the stable top-level paths after Demucs exits.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path


GPU_LOCK = Path("jobs/gpu.lock")


def write_status(job_dir: Path, payload: dict):
    p = job_dir / "status_isolate.json"
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
    p = job_dir / "status_isolate.json"
    logs: list = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, {"logs": logs[-200:]})


def find_demucs_stems(ep_dir: Path, ep: str) -> tuple[Path | None, Path | None]:
    """Locate Demucs nested output: htdemucs/{basename}/vocals.wav"""
    htdemucs = ep_dir / "htdemucs"
    if not htdemucs.exists():
        return None, None
    for subdir in htdemucs.iterdir():
        v = subdir / "vocals.wav"
        b = subdir / "no_vocals.wav"
        if v.exists():
            return v, b if b.exists() else None
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video",      required=True, help="Path to source MP4/MKV")
    ap.add_argument("--ep",         required=True, help="ep_folder slug")
    ap.add_argument("--output_dir", default="workspace/2_isolated",
                    help="Root isolated directory (ep sub-dir appended automatically)")
    args = ap.parse_args()

    video_path = Path(args.video)
    ep = args.ep
    iso_root = Path(args.output_dir)
    ep_dir = iso_root / ep
    ep_dir.mkdir(parents=True, exist_ok=True)

    job_dir = Path("jobs") / ep
    job_dir.mkdir(parents=True, exist_ok=True)

    # --- idempotency: skip if stable stems already exist ---
    stable_v = ep_dir / "vocals.wav"
    stable_b = ep_dir / "no_vocals.wav"
    if stable_v.exists() and stable_b.exists():
        write_status(job_dir, {"stage": "isolate", "status": "done", "progress": 100,
                               "error": None, "owner": "n8n"})
        print(f"[isolate] Already done — {stable_v}", flush=True)
        return

    # --- acquire GPU lock ---
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if GPU_LOCK.exists():
        holder = GPU_LOCK.read_text(encoding="utf-8").strip()
        print(f"[isolate] GPU busy: {holder}", file=sys.stderr, flush=True)
        sys.exit(2)  # 2 = GPU busy (route checks this)

    GPU_LOCK.write_text(f"isolate:{ep}", encoding="utf-8")

    write_status(job_dir, {"stage": "isolate", "status": "processing", "progress": 0,
                           "error": None, "logs": [], "owner": "n8n"})

    try:
        if not video_path.exists():
            raise FileNotFoundError(f"Source file not found: {video_path}")

        log(job_dir, f"Starting Demucs on {video_path.name}")

        # CORRECT: args list, never shell=True — brackets/spaces in filenames are safe
        cmd = [
            "python", "-m", "demucs",
            "--two-stems", "vocals",
            "-o", str(ep_dir),
            str(video_path),
        ]
        write_status(job_dir, {"progress": 5})
        result = subprocess.run(cmd, shell=False, capture_output=False)
        if result.returncode != 0:
            raise RuntimeError(f"Demucs exited with code {result.returncode}")

        write_status(job_dir, {"progress": 85})
        log(job_dir, "Demucs complete — copying stems to stable paths")

        # Copy nested htdemucs output to stable top-level paths
        nested_v, nested_b = find_demucs_stems(ep_dir, ep)
        if nested_v is None:
            raise FileNotFoundError(
                "Demucs finished but vocals.wav not found in htdemucs/ directory"
            )

        shutil.copy2(nested_v, stable_v)
        log(job_dir, f"vocals.wav → {stable_v}")

        if nested_b and nested_b.exists():
            shutil.copy2(nested_b, stable_b)
            log(job_dir, f"no_vocals.wav → {stable_b}")
            # Also keep the instrumental alias the spec references
            instrumental = ep_dir / "instrumental.wav"
            if not instrumental.exists():
                shutil.copy2(nested_b, instrumental)
        else:
            log(job_dir, "WARNING: no_vocals.wav not found from Demucs output")

        # Strip audio from source video → workspace/1_inputs/{ep}/video_no_audio.mp4
        # render_video.py reads from here so it never has to reach back into 0_raw_videos/
        inputs_dir = Path("workspace") / "1_inputs" / ep
        inputs_dir.mkdir(parents=True, exist_ok=True)
        muted_video = inputs_dir / "video_no_audio.mp4"
        if not muted_video.exists():
            log(job_dir, "Stripping audio track → 1_inputs/")
            strip_cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-an",          # drop audio
                "-c:v", "copy", # copy video stream, no re-encode
                str(muted_video),
            ]
            strip_result = subprocess.run(strip_cmd, shell=False, capture_output=True, text=True)
            if strip_result.returncode != 0:
                # Non-fatal — render falls back to original video
                log(job_dir, f"WARNING: ffmpeg strip failed: {strip_result.stderr[-200:]}")
            else:
                log(job_dir, f"video_no_audio.mp4 → {muted_video}")

        write_status(job_dir, {"status": "done", "progress": 100, "error": None})
        log(job_dir, "Isolation complete")

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, {"status": "error", "error": str(e), "progress": 0})
        sys.exit(1)
    finally:
        if GPU_LOCK.exists():
            try:
                GPU_LOCK.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
