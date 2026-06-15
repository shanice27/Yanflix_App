"""
pod_synth_handler.py — FastAPI server for RunPod IndexTTS2 synthesis Pod
========================================================================
Deploy this on a RunPod Pod (PyTorch 2.x + CUDA 12 template).
Exposes:
  POST /synthesize   — start a synthesis worker slice
  GET  /status       — poll current worker progress
  GET  /health       — liveness check

Setup on Pod:
  pip install fastapi uvicorn boto3 requests
  git clone https://github.com/index-tts/index-tts.git /workspace/IndexTTS2
  cd /workspace/IndexTTS2 && pip install -e .
  python handler.py
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import boto3
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

# ── R2 client (env vars injected by RunPod secrets or passed at startup) ──────
def _r2():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["CLOUDFLARE_R2_ENDPOINT"],
        aws_access_key_id=os.environ["CLOUDFLARE_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

R2_BUCKET = os.environ.get("CLOUDFLARE_R2_BUCKET", "yanflix")

# ── Global job state ───────────────────────────────────────────────────────────
_state: dict = {
    "status": "idle",
    "job_id": None,
    "track": None,
    "worker_id": 0,
    "line_start": None,
    "line_end": None,
    "progress": 0,
    "synthesized": 0,
    "errors": 0,
    "logs": [],
    "error": None,
    "r2_output_prefix": None,
}
_lock = threading.Lock()


class SynthRequest(BaseModel):
    ep_folder: str
    track_mode: str                  # standard | aave
    worker_id: int = 0
    worker_count: int = 1
    line_start: int | None = None    # override auto-split
    line_end:   int | None = None
    r2_state_key: str                # R2 key for state_director.json
    r2_refs_key: str                 # R2 key for refs.zip (all ref_*.wav files)
    r2_output_prefix: str            # R2 prefix where wav outputs go


@app.get("/health")
def health():
    return {"status": "ok", "gpu": _gpu_name()}


@app.get("/status")
def status():
    with _lock:
        return dict(_state)


@app.post("/synthesize")
def synthesize(req: SynthRequest):
    with _lock:
        if _state["status"] == "processing":
            raise HTTPException(status_code=409, detail="Worker already busy")
        _state.update({
            "status": "processing", "job_id": f"{req.ep_folder}_{req.track_mode}_w{req.worker_id}",
            "track": req.track_mode, "worker_id": req.worker_id,
            "line_start": req.line_start, "line_end": req.line_end,
            "progress": 0, "synthesized": 0, "errors": 0,
            "logs": [], "error": None,
            "r2_output_prefix": req.r2_output_prefix,
        })

    thread = threading.Thread(
        target=_run_synthesis, args=(req,), daemon=True
    )
    thread.start()
    return {"status": "processing", "job_id": _state["job_id"]}


def _log(msg: str):
    print(msg, flush=True)
    with _lock:
        _state["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        _state["logs"] = _state["logs"][-200:]


def _run_synthesis(req: SynthRequest):
    r2 = _r2()
    tmp = Path(tempfile.mkdtemp(prefix="yanflix_synth_"))
    try:
        # ── 1. Download state_director.json ──────────────────────────────────
        _log("Downloading state_director.json from R2…")
        state_path = tmp / "state_director.json"
        r2.download_file(R2_BUCKET, req.r2_state_key, str(state_path))

        # ── 2. Download and unzip ref wavs ───────────────────────────────────
        _log("Downloading ref wavs from R2…")
        refs_zip_path = tmp / "refs.zip"
        r2.download_file(R2_BUCKET, req.r2_refs_key, str(refs_zip_path))
        chars_root = tmp / "characters"
        with zipfile.ZipFile(refs_zip_path) as zf:
            zf.extractall(chars_root)
        _log(f"Extracted {len(list(chars_root.rglob('*.wav')))} ref wavs")

        # ── 3. Determine line slice ───────────────────────────────────────────
        state = json.loads(state_path.read_text())
        speech_lines = [l for l in state.get("lines", []) if l.get("type") == "speech"]
        total_lines = len(speech_lines)

        line_start = req.line_start
        line_end   = req.line_end
        if line_start is None and req.worker_count > 1:
            chunk = (total_lines + req.worker_count - 1) // req.worker_count
            line_start = req.worker_id * chunk
            line_end   = min(line_start + chunk - 1, total_lines - 1)
            # Convert slice indices to actual line_index values
            if line_start < len(speech_lines):
                line_start = speech_lines[line_start]["line_index"]
                line_end   = speech_lines[min(line_end, len(speech_lines)-1)]["line_index"]
            else:
                _log(f"Worker {req.worker_id} has no lines — done")
                with _lock:
                    _state.update({"status": "done", "progress": 100})
                return

        with _lock:
            _state.update({"line_start": line_start, "line_end": line_end})

        _log(f"Line slice: {line_start}–{line_end} for track={req.track_mode}")

        # ── 4. Run synthesize_dub.py ──────────────────────────────────────────
        out_dir = tmp / "tts_audio" / req.track_mode
        out_dir.mkdir(parents=True, exist_ok=True)

        script = Path("/workspace/yanflix/engine/synthesis/synthesize_dub.py")
        cmd = [
            sys.executable, str(script),
            "--job_dir", str(tmp),
            "--track_mode", req.track_mode,
            "--characters_root", str(chars_root),
            "--worker_id", str(req.worker_id),
        ]
        if line_start is not None:
            cmd += ["--line_start", str(line_start)]
        if line_end is not None:
            cmd += ["--line_end", str(line_end)]

        _log(f"Starting synthesize_dub.py…")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )

        # Stream synthesis logs + mirror progress from status file
        status_file = tmp / f"status_synth_{req.track_mode}.json"
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
                # Mirror progress from status file
                if status_file.exists():
                    try:
                        d = json.loads(status_file.read_text())
                        with _lock:
                            _state["progress"] = d.get("progress", 0)
                            _state["synthesized"] = d.get("result", {}).get("synthesized", 0)
                            _state["errors"] = d.get("result", {}).get("errors", 0)
                    except Exception:
                        pass

        proc.wait()

        # ── 5. Upload results to R2 ───────────────────────────────────────────
        wav_files = list(out_dir.glob("raw_line_*.wav"))
        _log(f"Uploading {len(wav_files)} wav files to R2…")
        for wav in wav_files:
            r2_key = f"{req.r2_output_prefix}/{req.track_mode}/{wav.name}"
            r2.upload_file(str(wav), R2_BUCKET, r2_key)

        _log(f"Upload complete — {len(wav_files)} files")

        if proc.returncode != 0:
            raise RuntimeError(f"synthesize_dub.py exited with code {proc.returncode}")

        with _lock:
            _state.update({
                "status": "done", "progress": 100,
                "wav_count": len(wav_files),
            })
        _log("Worker done")

    except Exception as e:
        _log(f"ERROR: {e}")
        with _lock:
            _state.update({"status": "error", "error": str(e)})
    finally:
        # Clean up tmp dir
        import shutil
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


def _gpu_name() -> str:
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
