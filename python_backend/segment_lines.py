"""
segment_lines.py — Yanflix Stage: Line Segmentation (CPU only)
===============================================================
Slices the isolated vocal stem into per-line WAV clips using the timestamps in
state_director.json, with silence-boundary snapping so clips don't cut
mid-word. Also extracts intro/outro song segments from BOTH stems.

For each speech line:
  1. Take [start, end] from the line.
  2. Expand the window by PAD seconds on each side.
  3. Within the expanded window, snap the cut points outward/inward to the
     nearest silence (energy below threshold) so the clip starts and ends in
     quiet, not mid-syllable.
  4. Export jobs/{ep}/line_clips/line_NNN.wav and write clip_path into the
     line's entry in state_director.json (atomic write-through).

For each entry in state["songs"] (and any line with type=="singing" that has
no matching songs[] entry):
  Export the segment from BOTH the vocal stem and the instrumental stem to
  jobs/{ep}/songs/{segment}_vocals.wav and {segment}_instrumental.wav.

Resumable: lines whose clip_path already exists on disk are skipped.

Usage:
  conda run -n dubbing python python_backend/segment_lines.py \
      --job_dir ./jobs/smoking_behind_the_supermarket_with_you_s01e01 \
      --vocals ./workspace/2_isolated/smoking_behind_the_supermarket_with_you_s01e01/vocals.wav \
      --instrumental ./workspace/2_isolated/smoking_behind_the_supermarket_with_you_s01e01/no_vocals.wav

Writes: {job_dir}/line_clips/line_NNN.wav
        {job_dir}/songs/{segment}_{vocals,instrumental}.wav
        {job_dir}/status_segment.json
        state_director.json (clip_path per line, merged atomically)
"""

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

try:
    from pydub import AudioSegment  # type: ignore[import-untyped]
    from pydub.silence import detect_silence  # type: ignore[import-untyped]
except ImportError:
    print("FATAL: pydub is required (pip install pydub; ffmpeg must be on PATH)",
          file=sys.stderr)
    sys.exit(1)


# ---------------- status helpers ----------------
def write_status(job_dir: Path, payload: dict):
    p = job_dir / "status_segment.json"
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
    p = job_dir / "status_segment.json"
    logs = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, {"logs": logs[-300:]})


def save_state(state_path: Path, state: dict):
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)


# ---------------- silence snapping ----------------
def snap_boundaries(audio: AudioSegment, start_ms: int, end_ms: int,
                    pad_ms: int, silence_thresh_db: int, min_silence_ms: int):
    """
    Returns (snapped_start_ms, snapped_end_ms).

    Looks in the padding window BEFORE start for the last silence and snaps the
    start to its middle; looks in the padding window AFTER end for the first
    silence and snaps the end to its middle. If no silence is found in a
    window, falls back to the padded boundary.
    """
    total = len(audio)
    win_start_lo = max(0, start_ms - pad_ms)
    win_end_hi = min(total, end_ms + pad_ms)

    new_start = win_start_lo
    pre = audio[win_start_lo:min(start_ms + min_silence_ms, total)]
    if len(pre) > min_silence_ms:
        sil = detect_silence(pre, min_silence_len=min_silence_ms,
                             silence_thresh=silence_thresh_db, seek_step=10)
        if sil:
            s, e = sil[-1]  # last silence before speech begins
            new_start = win_start_lo + (s + e) // 2

    new_end = win_end_hi
    post = audio[max(end_ms - min_silence_ms, 0):win_end_hi]
    post_offset = max(end_ms - min_silence_ms, 0)
    if len(post) > min_silence_ms:
        sil = detect_silence(post, min_silence_len=min_silence_ms,
                             silence_thresh=silence_thresh_db, seek_step=10)
        if sil:
            s, e = sil[0]  # first silence after speech ends
            new_end = post_offset + (s + e) // 2

    if new_end <= new_start + 200:  # degenerate — keep padded window
        return win_start_lo, win_end_hi
    return new_start, new_end


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job_dir", required=True)
    ap.add_argument("--vocals", required=True, help="isolated vocal stem wav")
    ap.add_argument("--instrumental", default="", help="instrumental stem wav (for songs)")
    ap.add_argument("--pad_ms", type=int, default=250,
                    help="search window beyond each timestamp for silence snapping")
    ap.add_argument("--silence_thresh_db", type=int, default=-38)
    ap.add_argument("--min_silence_ms", type=int, default=120)
    ap.add_argument("--export_sr", type=int, default=24000,
                    help="sample rate for exported clips (mono)")
    args = ap.parse_args()

    job_dir = Path(args.job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_status(job_dir, {"stage": "segment", "status": "processing",
                           "progress": 0, "error": None, "logs": []})
    try:
        state_path = job_dir / "state_director.json"
        if not state_path.exists():
            raise FileNotFoundError(f"{state_path} not found — run script-director first")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        lines = state.get("lines", [])
        if not lines:
            raise RuntimeError("state_director.json has no lines[]")

        vocals_path = Path(args.vocals)
        if not vocals_path.exists():
            raise FileNotFoundError(f"vocal stem not found: {vocals_path}")
        log(job_dir, f"Loading vocal stem: {vocals_path}")
        vocals = AudioSegment.from_file(str(vocals_path)) \
            .set_channels(1).set_frame_rate(args.export_sr)
        total_ms = len(vocals)

        clips_dir = job_dir / "line_clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        speech = [ln for ln in lines if ln.get("type", "speech") == "speech"]
        log(job_dir, f"{len(speech)} speech lines to clip ({total_ms/1000:.1f}s of vocals)")

        done = skipped = errors = 0
        for i, ln in enumerate(speech):
            idx = ln["line_index"]
            out = clips_dir / f"line_{idx:03d}.wav"
            try:
                if out.exists() and ln.get("clip_path"):
                    skipped += 1
                else:
                    start_ms = int(float(ln["start"]) * 1000)
                    end_ms = int(float(ln["end"]) * 1000)
                    if end_ms <= start_ms or start_ms >= total_ms:
                        raise ValueError(f"bad timestamps {ln['start']}–{ln['end']}")
                    end_ms = min(end_ms, total_ms)
                    s, e = snap_boundaries(vocals, start_ms, end_ms, args.pad_ms,
                                           args.silence_thresh_db, args.min_silence_ms)
                    vocals[s:e].export(str(out), format="wav")
                    done += 1
                ln["clip_path"] = str(out)
            except Exception as ex:
                errors += 1
                ln["error_msg"] = f"segment: {ex}"
                log(job_dir, f"line {idx:03d}: ERROR {ex}")

            if (i + 1) % 25 == 0 or i + 1 == len(speech):
                save_state(state_path, state)  # write-through in batches of 25
                write_status(job_dir, {"progress": int(90 * (i + 1) / max(len(speech), 1))})

        # ---------------- song segments ----------------
        songs_dir = job_dir / "songs"
        song_entries = list(state.get("songs", []))
        # also pick up singing-type lines that have no songs[] entry
        known_spans = {(round(s.get("start", -1), 2), round(s.get("end", -1), 2))
                       for s in song_entries}
        for ln in lines:
            if ln.get("type") == "singing":
                span = (round(float(ln["start"]), 2), round(float(ln["end"]), 2))
                if span not in known_spans:
                    song_entries.append({"segment": f"song_line_{ln['line_index']:03d}",
                                         "start": ln["start"], "end": ln["end"]})

        if song_entries:
            songs_dir.mkdir(parents=True, exist_ok=True)
            instrumental = None
            if args.instrumental and Path(args.instrumental).exists():
                instrumental = AudioSegment.from_file(args.instrumental).set_channels(2)
            elif args.instrumental:
                log(job_dir, f"WARNING: instrumental stem not found: {args.instrumental} "
                             "— song instrumental extraction skipped")
            for s in song_entries:
                seg = s.get("segment", "song")
                a = int(float(s["start"]) * 1000)
                b = int(float(s["end"]) * 1000)
                v_out = songs_dir / f"{seg}_vocals.wav"
                if not v_out.exists():
                    vocals[a:min(b, total_ms)].export(str(v_out), format="wav")
                s["vocals_wav"] = str(v_out)
                if instrumental is not None:
                    i_out = songs_dir / f"{seg}_instrumental.wav"
                    if not i_out.exists():
                        instrumental[a:min(b, len(instrumental))].export(str(i_out), format="wav")
                    s["instrumental_wav"] = str(i_out)
                log(job_dir, f"song '{seg}': {s['start']}–{s['end']}s extracted")
            state["songs"] = [s for s in song_entries]

        save_state(state_path, state)
        result = {"clipped": done, "skipped": skipped, "errors": errors,
                  "songs": len(song_entries)}
        write_status(job_dir, {"status": "done" if done + skipped > 0 else "error",
                               "progress": 100, "result": result,
                               "error": None if done + skipped > 0 else "no clips produced"})
        log(job_dir, f"Segmentation complete: {result}")
        if done + skipped == 0:
            sys.exit(1)

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, {"status": "error", "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
