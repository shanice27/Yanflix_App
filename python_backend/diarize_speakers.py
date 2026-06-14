"""
diarize_speakers.py
Run pyannote speaker diarization on vocals.wav, map voice clusters to
whisper segments, estimate gender per cluster via pitch analysis, then
use Groq to map speaker IDs → character names.

Writes:
  jobs/{ep_folder}/state_director.json  — adds speaker_id + gender per line
  jobs/{ep_folder}/status_diarize_speakers.json
"""

import argparse, json, os, sys, time, urllib.request, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm_client import chat as llm_chat

import numpy as np

GPU_LOCK = Path("jobs/gpu.lock")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ep_folder",  required=True)
    p.add_argument("--vocals",     required=True)
    p.add_argument("--job_dir",    required=True)
    p.add_argument("--hf_token",   required=True)
    p.add_argument("--groq_api_key", default="")
    p.add_argument("--show_name",  default="")
    return p.parse_args()

# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
def write_status(job_dir, payload):
    p = Path(job_dir) / "status_diarize_speakers.json"
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({**payload, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f, indent=2)
    os.replace(tmp, p)

def atomic_write_json(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

# ---------------------------------------------------------------------------
# 1. Pyannote speaker diarization
# ---------------------------------------------------------------------------
def run_pyannote(vocals_path, hf_token, job_dir):
    from pyannote.audio import Pipeline
    import torch

    write_status(job_dir, {"stage": "diarize_speakers", "status": "processing", "step": "pyannote_loading", "progress": 5})
    print("[diarize_speakers] Loading pyannote pipeline…", flush=True)

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline = pipeline.to(torch.device(device))
    print(f"[diarize_speakers] Running pyannote on {vocals_path} ({device})…", flush=True)
    write_status(job_dir, {"stage": "diarize_speakers", "status": "processing", "step": "pyannote_running", "progress": 15})

    diarization = pipeline(vocals_path)

    # Build list of (start, end, speaker_label)
    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append((turn.start, turn.end, speaker))

    print(f"[diarize_speakers] Pyannote found {len(set(s[2] for s in segments))} speakers, {len(segments)} segments", flush=True)
    return segments

# ---------------------------------------------------------------------------
# 2. Map pyannote segments → whisper lines (longest overlap wins)
# ---------------------------------------------------------------------------
def map_to_lines(pyannote_segs, lines):
    def overlap(a_start, a_end, b_start, b_end):
        return max(0.0, min(a_end, b_end) - max(a_start, b_start))

    for line in lines:
        ls, le = line.get("start", 0), line.get("end", 0)
        best_speaker, best_ov = None, 0.0
        for ps, pe, spk in pyannote_segs:
            ov = overlap(ls, le, ps, pe)
            if ov > best_ov:
                best_ov = ov
                best_speaker = spk
        line["speaker_id"] = best_speaker or "SPEAKER_UNKNOWN"

    return lines

# ---------------------------------------------------------------------------
# 3. Gender estimation via pitch (F0) per speaker
#    Processes one sampled clip per speaker — not per whisper line.
# ---------------------------------------------------------------------------
def estimate_gender(vocals_path, pyannote_segs):
    """
    pyannote_segs: list of (start, end, speaker_label)
    Loads audio once, takes up to 10 segments per speaker, runs librosa.yin
    (fast) on each, aggregates median F0 per speaker.
    """
    import librosa

    print("[diarize_speakers] Loading audio for pitch analysis…", flush=True)
    y, sr = librosa.load(vocals_path, sr=16000, mono=True)
    print(f"[diarize_speakers] Audio loaded ({len(y)/sr:.1f}s). Estimating pitch per speaker…", flush=True)

    # Group pyannote segments by speaker, keep only segments ≥ 1.5s
    spk_segs: dict[str, list] = {}
    for ps, pe, spk in pyannote_segs:
        if pe - ps >= 1.5:
            spk_segs.setdefault(spk, []).append((ps, pe))

    gender_map: dict[str, str] = {}
    median_f0:  dict[str, float] = {}

    for spk, segs in spk_segs.items():
        # Sample up to 10 of their longest segments
        segs_sorted = sorted(segs, key=lambda x: x[1] - x[0], reverse=True)[:10]
        all_f0 = []
        for ps, pe in segs_sorted:
            start_s = int(ps * sr)
            end_s   = min(int(pe * sr), len(y))
            clip    = y[start_s:end_s]
            if len(clip) < sr * 1.0:
                continue
            try:
                # yin is 5-10x faster than pyin, accurate enough for gender
                f0 = librosa.yin(clip, fmin=60, fmax=400, sr=sr,
                                 frame_length=1024, hop_length=256)
                voiced = f0[(f0 > 60) & (f0 < 400)]
                all_f0.extend(voiced.tolist())
            except Exception:
                pass

        if not all_f0:
            gender_map[spk] = "unknown"
            median_f0[spk] = 0.0
            continue

        med = float(np.median(all_f0))
        median_f0[spk] = round(med, 1)
        gender_map[spk] = "female" if med > 155 else "male"
        print(f"[diarize_speakers]   {spk}: median F0 = {med:.1f} Hz → {gender_map[spk]}", flush=True)

    return gender_map, median_f0

# ---------------------------------------------------------------------------
# 4. Groq: map speaker IDs → character names
# ---------------------------------------------------------------------------
def groq_name_speakers(groq_key, lines, gender_map, show_name):
    """Build a compact per-speaker summary and ask the LLM fallback chain to name them."""
    # Collect sample lines per speaker
    speaker_samples: dict[str, list[str]] = {}
    speaker_line_count: dict[str, int] = {}
    for l in lines:
        spk = l.get("speaker_id", "UNKNOWN")
        txt = l.get("source_text", "")
        speaker_line_count[spk] = speaker_line_count.get(spk, 0) + 1
        if len(speaker_samples.get(spk, [])) < 5 and txt:
            speaker_samples.setdefault(spk, []).append(txt)

    # Build prompt
    spk_descriptions = []
    for spk, count in sorted(speaker_line_count.items(), key=lambda x: -x[1]):
        gender = gender_map.get(spk, "unknown")
        samples = speaker_samples.get(spk, [])
        spk_descriptions.append({
            "speaker_id": spk,
            "line_count": count,
            "estimated_gender": gender,
            "sample_lines": samples,
        })

    prompt = f"""You are a casting director for the anime "{show_name}".
I ran voice analysis on the episode audio and identified {len(spk_descriptions)} distinct speakers.
Each has an estimated gender (from pitch analysis) and sample dialogue lines (in Japanese).

Your job: assign a character name to each speaker_id.
Use lowercase_underscore names (e.g. "sasaki", "yamada_taro", "store_clerk").
Use context clues from the dialogue samples and the show name.
If you can't determine a name, use "unknown_speaker_N".

Speakers:
{json.dumps(spk_descriptions, ensure_ascii=False, indent=2)}

Return ONLY valid JSON — an array of objects:
[{{"speaker_id": "SPEAKER_00", "character": "sasaki", "reasoning": "brief note"}}]
One entry per speaker. No markdown."""

    text = llm_chat(
        messages=[{"role": "user", "content": prompt}],
        json_mode=True,
        temperature=0.1,
        max_tokens=1024,
    )

    # Parse — Groq wraps in an object sometimes
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        parsed = parsed.get("speakers") or parsed.get("assignments") or list(parsed.values())[0]
    return parsed  # list of {speaker_id, character, reasoning}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    job_dir  = Path(args.job_dir)
    state_path = job_dir / "state_director.json"

    if not state_path.exists():
        print(f"[diarize_speakers] ERROR: {state_path} not found", file=sys.stderr)
        sys.exit(1)

    # GPU lock
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if GPU_LOCK.exists():
        print("[diarize_speakers] GPU busy, exiting", file=sys.stderr)
        sys.exit(2)

    try:
        GPU_LOCK.write_text(f"diarize_speakers:{args.ep_folder}")

        state = json.loads(state_path.read_text(encoding="utf-8"))
        lines = state.get("lines", [])

        # 1. Pyannote
        write_status(job_dir, {"stage": "diarize_speakers", "status": "processing", "step": "pyannote", "progress": 10})
        pyannote_segs = run_pyannote(args.vocals, args.hf_token, str(job_dir))

        # 2. Map to lines
        write_status(job_dir, {"stage": "diarize_speakers", "status": "processing", "step": "mapping", "progress": 50})
        lines = map_to_lines(pyannote_segs, lines)
        unique_speakers = sorted(set(l.get("speaker_id", "") for l in lines))
        print(f"[diarize_speakers] Speakers assigned to lines: {unique_speakers}", flush=True)

        # 3. Gender estimation — pass pyannote segments, not whisper lines
        write_status(job_dir, {"stage": "diarize_speakers", "status": "processing", "step": "gender_analysis", "progress": 65})
        gender_map, median_f0 = estimate_gender(args.vocals, pyannote_segs)

        # 4. Groq: name the speakers
        if args.groq_api_key:
            write_status(job_dir, {"stage": "diarize_speakers", "status": "processing", "step": "naming", "progress": 80})
            print("[diarize_speakers] Asking Groq to name speakers…", flush=True)
            try:
                assignments = groq_name_speakers(args.groq_api_key, lines, gender_map, args.show_name or args.ep_folder)
                # Build speaker_id → character map
                char_map = {a["speaker_id"]: a["character"] for a in assignments}
                reasoning = {a["speaker_id"]: a.get("reasoning", "") for a in assignments}
                print(f"[diarize_speakers] Name assignments: {char_map}", flush=True)
                # Apply character names
                for l in lines:
                    spk = l.get("speaker_id", "")
                    if spk in char_map:
                        l["character"] = char_map[spk]
            except Exception as e:
                print(f"[diarize_speakers] Groq naming failed: {e} — keeping speaker IDs", file=sys.stderr)
                char_map = {}
                reasoning = {}
        else:
            char_map = {}
            reasoning = {}

        # 5. Write updated state
        state["lines"] = lines
        state["speaker_gender_map"] = gender_map
        state["speaker_f0_map"] = median_f0
        state["speaker_char_map"] = char_map
        state["speaker_reasoning"] = reasoning
        state["cast_locked"] = False  # reset so user can review
        atomic_write_json(state_path, state)

        write_status(job_dir, {
            "stage": "diarize_speakers", "status": "done", "progress": 100,
            "speakers": len(unique_speakers),
            "gender_map": gender_map,
            "char_map": char_map,
        })
        print(f"[diarize_speakers] Done — {len(unique_speakers)} speakers, {len(lines)} lines updated", flush=True)

    finally:
        if GPU_LOCK.exists():
            GPU_LOCK.unlink()

if __name__ == "__main__":
    main()
