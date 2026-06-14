"""
render_video.py — FFmpeg final mux (CPU)
=========================================
Reads state_director.json, overlays every fit_line_NNN.wav at its timestamp
over the instrumental stem, copies the original video stream (no re-encode).

Song resolution per entry:
  1. song_source == "cache"        → characters/shows/{show}/songs/{seg}_{track}.wav
  2. song_source == "generate" AND status == "song_complete" → dubbed_wav path
  3. Neither                       → original audio passthrough (no dub yet)

Filtergraph is chunked ~50 lines to keep FFmpeg command manageable.

Args:
  --job_dir       jobs/{ep_folder}/
  --track_mode    standard | aave
  --show          show slug
  --characters_root  path to characters/
  --output_dir    workspace/5_outputs/

Writes:
  {output_dir}/{ep_folder}_{track_mode}.mp4
  {job_dir}/status_render_{track_mode}.json
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def write_status(job_dir: Path, track: str, payload: dict):
    p = job_dir / f"status_render_{track}.json"
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
    p = job_dir / f"status_render_{track}.json"
    logs: list = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, track, {"logs": logs[-200:]})


def build_audio_filter(fit_wavs: list[tuple[str, float]], bg_wav: str,
                       bg_vol: float = 0.3, dub_vol: float = 1.0,
                       chunk_size: int = 50) -> tuple[list[str], str]:
    """
    Build FFmpeg inputs and filtergraph for adelay/amix overlay.
    Returns (extra_input_args, filter_complex_string).
    Chunked in groups of chunk_size to keep filtergraph manageable.
    """
    extra_inputs: list[str] = ["-i", bg_wav]
    # bg = input 1 (input 0 is the video)
    bg_idx = 1
    line_idx_base = 2  # speech line wav inputs start at 2

    filter_parts: list[str] = []

    for wav_path, start_s in fit_wavs:
        extra_inputs += ["-i", wav_path]

    n = len(fit_wavs)
    if n == 0:
        # No dialogue — just use bg audio as-is
        return extra_inputs, f"[{bg_idx}]volume={bg_vol}[out]"

    # Build chunk-based amix tree
    streams: list[str] = [f"[{bg_idx}]volume={bg_vol}[bg_v]", "[bg_v]"]

    chunk_outputs: list[str] = []
    for chunk_start in range(0, n, chunk_size):
        chunk = fit_wavs[chunk_start: chunk_start + chunk_size]
        chunk_tag = f"chunk_{chunk_start}"
        parts_for_chunk: list[str] = []

        for local_i, (wav_path, start_s) in enumerate(chunk):
            global_i = chunk_start + local_i
            inp_idx = line_idx_base + global_i
            delay_ms = int(start_s * 1000)
            tag = f"d{global_i}"
            filter_parts.append(
                f"[{inp_idx}]volume={dub_vol},adelay={delay_ms}|{delay_ms}[{tag}]"
            )
            parts_for_chunk.append(f"[{tag}]")

        # amix this chunk with the running bg stream
        n_in = len(parts_for_chunk) + 1
        mix_in = "[bg_v]" if chunk_start == 0 else f"[mix_{chunk_start - chunk_size}]"
        mix_out = f"[mix_{chunk_start}]" if (chunk_start + chunk_size) < n else "[out]"
        filter_parts.append(
            f"{mix_in}{''.join(parts_for_chunk)}amix=inputs={n_in}:duration=longest:normalize=0{mix_out}"
        )

    return extra_inputs, ";".join(filter_parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job_dir",         required=True)
    ap.add_argument("--track_mode",      required=True, choices=["standard", "aave"])
    ap.add_argument("--show",            default="")
    ap.add_argument("--characters_root", default="characters")
    ap.add_argument("--output_dir",      default="workspace/5_outputs")
    ap.add_argument("--bg_vol",          type=float, default=0.3)
    ap.add_argument("--dub_vol",         type=float, default=1.0)
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    track = args.track_mode
    ep = job_dir.name
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{ep}_{track}.mp4"
    chars_root = Path(args.characters_root)
    show_slug = args.show

    write_status(job_dir, track, {
        "stage": f"render_{track}", "status": "processing",
        "progress": 0, "error": None, "logs": [],
    })

    try:
        state_path = job_dir / "state_director.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))

        # --- Locate source video for video stream copy ---
        # Prefer the pre-stripped muted video from 1_inputs/ (written by isolate.py).
        # Fall back to original in 0_raw_videos/ via meta.json if not available.
        muted = Path("workspace") / "1_inputs" / ep / "video_no_audio.mp4"
        video_src: Path | None = muted if muted.exists() else None

        if video_src is None:
            meta_path = job_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    vp = meta.get("video_path", "")
                    if vp and Path(vp).exists():
                        video_src = Path(vp)
                except Exception:
                    pass

        # Instrumental stem (background audio)
        iso_dir = Path("workspace") / "2_isolated" / ep
        bg_wav = iso_dir / "no_vocals.wav"
        if not bg_wav.exists():
            bg_wav = iso_dir / "instrumental.wav"
        if not bg_wav.exists():
            raise FileNotFoundError(f"Instrumental audio not found at {iso_dir}")

        log(job_dir, track, f"Building audio timeline for {track} track …")
        write_status(job_dir, track, {"progress": 10})

        # --- Collect fit WAVs for speech lines ---
        fit_wavs: list[tuple[str, float]] = []
        for line in state.get("lines", []):
            if line.get("type") != "speech":
                continue
            fit_path_str = (line.get("fit_wav") or {}).get(track, "")
            if not fit_path_str:
                # Fallback: standard naming
                idx = line["line_index"]
                fit_path_str = str(job_dir / "tts_audio" / track / f"fit_line_{idx:03d}.wav")
            if not Path(fit_path_str).exists():
                log(job_dir, track, f"WARNING: fit_wav missing for line {line['line_index']}")
                continue
            start_s = float(line["start"])
            fit_wavs.append((fit_path_str, start_s))

        log(job_dir, track, f"{len(fit_wavs)} fit wavs collected")
        write_status(job_dir, track, {"progress": 20})

        # --- Song WAVs ---
        song_wav_inputs: list[tuple[str, float]] = []
        for song in state.get("songs", []):
            start_s = float(song.get("start", 0))
            seg = song.get("segment", "")
            src = song.get("song_source", "generate")

            if src == "cache":
                vault_path = chars_root / "shows" / show_slug / "songs" / f"{seg}_{track}.wav"
                if vault_path.exists():
                    song_wav_inputs.append((str(vault_path), start_s))
                    continue

            dubbed_wav = song.get("dubbed_wav", "")
            if dubbed_wav and Path(dubbed_wav).exists() and song.get("status") == "song_complete":
                song_wav_inputs.append((dubbed_wav, start_s))
                continue

            log(job_dir, track, f"Song '{seg}' not dubbed — original audio will play through")

        # Merge song + dialogue, sorted by start time
        all_audio = sorted(fit_wavs + song_wav_inputs, key=lambda x: x[1])

        write_status(job_dir, track, {"progress": 30})

        # --- Build FFmpeg command ---
        # Input 0 = source video (for video stream copy) or dummy if no video
        # Input 1 = instrumental
        # Inputs 2+ = fit wavs

        extra_inputs, filter_complex = build_audio_filter(
            all_audio, str(bg_wav),
            bg_vol=args.bg_vol, dub_vol=args.dub_vol,
        )

        if video_src and video_src.exists():
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(video_src)] + extra_inputs
            ffmpeg_cmd += ["-filter_complex", filter_complex]
            ffmpeg_cmd += ["-map", "0:v", "-map", "[out]"]
            ffmpeg_cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
        else:
            # No video source — render audio-only MP4
            ffmpeg_cmd = ["ffmpeg", "-y"] + extra_inputs
            ffmpeg_cmd += ["-filter_complex", filter_complex]
            ffmpeg_cmd += ["-map", "[out]"]
            ffmpeg_cmd += ["-c:a", "aac", "-b:a", "192k"]

        ffmpeg_cmd.append(str(output_path))

        log(job_dir, track, f"Running FFmpeg → {output_path.name}")
        write_status(job_dir, track, {"progress": 40})

        # CORRECT: args list — never shell=True, paths with spaces safe
        result = subprocess.run(ffmpeg_cmd, shell=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg failed (code {result.returncode}):\n{result.stderr[-2000:]}"
            )

        write_status(job_dir, track, {
            "status": "done", "progress": 100, "error": None,
            "output": str(output_path),
        })
        log(job_dir, track, f"Render complete → {output_path}")

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, track, {"status": "error", "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
