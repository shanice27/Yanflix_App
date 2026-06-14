"""
transcribe.py — Faster-Whisper transcription (GPU, env: dubbing)
=================================================================
Args:
  --vocals      path to vocals.wav (stable isolated stem)
  --job_dir     jobs/{ep_folder}/
  --source_lang ISO 639-1 code (ja, ko, zh, en, ...)
  --output      output JSON path (jobs/{ep}/state_whisper.json)
  --hf_token    optional HuggingFace token (for pyannote, if ever used)

Model: faster-whisper medium, compute_type=int8 (6GB VRAM safe)

Writes:
  {output}  (timestamped segment array)
  {job_dir}/status_transcribe.json
  Removes jobs/gpu.lock in finally block
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

GPU_LOCK = Path("jobs/gpu.lock")


def write_status(job_dir: Path, payload: dict):
    p = job_dir / "status_transcribe.json"
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
    p = job_dir / "status_transcribe.json"
    logs: list = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, {"logs": logs[-200:]})


CHUNK_SECONDS = 120  # 2-min chunks — safely under 25MB for any WAV format


def groq_transcribe(vocals_path: Path, source_lang: str, api_key: str, job_dir: Path) -> list:
    """Chunk vocals.wav into 2-min segments, POST each to Groq Whisper, merge with offsets."""
    import urllib.request, urllib.error, tempfile, email.mime.multipart, mimetypes

    # Get total duration via ffprobe
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(vocals_path),
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True, shell=False)
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {probe.stderr[-200:]}")
    total_duration = float(probe.stdout.strip())
    log(job_dir, f"Audio duration: {total_duration:.1f}s, splitting into {CHUNK_SECONDS}s chunks")

    chunks_start = list(range(0, int(total_duration), CHUNK_SECONDS))
    all_segments = []
    seg_id = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for chunk_idx, start in enumerate(chunks_start):
            end = min(start + CHUNK_SECONDS, total_duration)
            chunk_path = Path(tmpdir) / f"chunk_{chunk_idx:03d}.wav"

            # Extract chunk with ffmpeg
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", str(vocals_path),
                "-ss", str(start), "-to", str(end),
                "-c", "copy",
                str(chunk_path),
            ]
            r = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, shell=False)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg chunk {chunk_idx} failed: {r.stderr[-200:]}")

            pct = 5 + int(85 * chunk_idx / len(chunks_start))
            write_status(job_dir, {"progress": pct})
            log(job_dir, f"Groq Whisper chunk {chunk_idx+1}/{len(chunks_start)} ({start:.0f}s-{end:.0f}s)")

            # POST to Groq using multipart/form-data via urllib
            chunk_bytes = chunk_path.read_bytes()
            boundary = b"----WavBoundary"
            body = (
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="file"; filename="chunk.wav"\r\n'
                b"Content-Type: audio/wav\r\n\r\n"
                + chunk_bytes
                + b"\r\n--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="model"\r\n\r\n'
                b"whisper-large-v3-turbo"
                + b"\r\n--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="language"\r\n\r\n'
                + source_lang.encode()
                + b"\r\n--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="response_format"\r\n\r\n'
                b"verbose_json"
                + b"\r\n--" + boundary + b"--\r\n"
            )
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
                },
                method="POST",
            )

            # Retry once on 429
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=300) as resp:
                        data = json.loads(resp.read())
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 2:
                        log(job_dir, f"Groq 429 on chunk {chunk_idx+1} — waiting 30s")
                        time.sleep(30)
                    else:
                        raise

            for seg in data.get("segments", []):
                all_segments.append({
                    "id":    seg_id,
                    "start": round(seg["start"] + start, 3),
                    "end":   round(seg["end"]   + start, 3),
                    "text":  seg["text"].strip(),
                    "words": [
                        {"word": w["word"], "start": round(w["start"] + start, 3),
                         "end": round(w["end"] + start, 3), "probability": round(w.get("probability", 1.0), 4)}
                        for w in seg.get("words", [])
                    ],
                })
                seg_id += 1

    log(job_dir, f"Groq Whisper done — {len(all_segments)} segments across {len(chunks_start)} chunks")
    return all_segments


def local_whisper_transcribe(vocals_path: Path, source_lang: str, job_dir: Path) -> list:
    """Fallback: local Faster-Whisper on GPU (acquires GPU lock)."""
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if GPU_LOCK.exists():
        holder = GPU_LOCK.read_text(encoding="utf-8").strip()
        print(f"[transcribe] GPU busy: {holder}", file=sys.stderr, flush=True)
        sys.exit(2)

    ep = job_dir.name
    GPU_LOCK.write_text(f"transcribe:{ep}", encoding="utf-8")
    log(job_dir, "Falling back to local Faster-Whisper (GPU)...")

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("medium", device="cuda", compute_type="int8")
        log(job_dir, f"Transcribing {vocals_path.name} locally (lang={source_lang})")
        write_status(job_dir, {"progress": 10})

        segments, info = model.transcribe(
            str(vocals_path), language=source_lang, beam_size=5, word_timestamps=True,
        )
        segment_list = []
        for i, seg in enumerate(segments):
            segment_list.append({
                "id": i, "start": round(seg.start, 3), "end": round(seg.end, 3),
                "text": seg.text.strip(),
                "words": [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3),
                           "probability": round(w.probability, 4)} for w in (seg.words or [])],
            })
            if i % 20 == 0:
                write_status(job_dir, {"progress": min(10 + int(85 * i / max(i+1, 1)), 95)})

        del model
        try:
            import torch; torch.cuda.empty_cache()
        except Exception:
            pass

        log(job_dir, f"Local Whisper done — {len(segment_list)} segments, lang={info.language}")
        return segment_list
    finally:
        if GPU_LOCK.exists():
            try:
                GPU_LOCK.unlink()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocals",        required=True, help="Path to vocals.wav")
    ap.add_argument("--job_dir",       required=True)
    ap.add_argument("--source_lang",   required=True, help="ISO 639-1 source language")
    ap.add_argument("--output",        required=True, help="Path for output JSON")
    ap.add_argument("--hf_token",      default="",   help="HuggingFace token (optional)")
    ap.add_argument("--groq_api_key",  default="",   help="Groq API key for cloud Whisper")
    args = ap.parse_args()

    vocals_path = Path(args.vocals)
    job_dir = Path(args.job_dir)
    output_path = Path(args.output)
    job_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- idempotency ---
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
            if existing:
                write_status(job_dir, {"stage": "transcribe", "status": "done",
                                       "progress": 100, "error": None, "owner": "n8n"})
                print("[transcribe] Already done — skipping", flush=True)
                return
        except Exception:
            pass

    if not vocals_path.exists():
        print(f"[transcribe] vocals.wav not found: {vocals_path}", file=sys.stderr, flush=True)
        sys.exit(1)

    write_status(job_dir, {"stage": "transcribe", "status": "processing", "progress": 0,
                           "error": None, "logs": [], "owner": "n8n"})

    groq_key = args.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    segment_list = []

    try:
        if groq_key:
            # Primary: Groq Whisper (cloud, no GPU lock needed)
            log(job_dir, "Using Groq Whisper (cloud, no GPU required)")
            try:
                segment_list = groq_transcribe(vocals_path, args.source_lang, groq_key, job_dir)
            except Exception as e:
                log(job_dir, f"Groq Whisper failed ({e}) — falling back to local GPU Whisper")
                segment_list = local_whisper_transcribe(vocals_path, args.source_lang, job_dir)
        else:
            # Fallback: local Faster-Whisper
            segment_list = local_whisper_transcribe(vocals_path, args.source_lang, job_dir)

        # Atomic write
        tmp = output_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(segment_list, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, output_path)

        write_status(job_dir, {"status": "done", "progress": 100, "error": None,
                               "segment_count": len(segment_list)})

        # Mirror to 3_transcripts/
        transcripts_dir = Path("workspace") / "3_transcripts" / job_dir.name
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        mirror = transcripts_dir / "transcript.json"
        tmp_m = mirror.with_suffix(".json.tmp")
        tmp_m.write_text(json.dumps(segment_list, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_m, mirror)
        log(job_dir, f"transcript.json mirrored to 3_transcripts/")

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, {"status": "error", "error": str(e), "progress": 0})
        sys.exit(1)


if __name__ == "__main__":
    main()
