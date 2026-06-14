"""
_whisper_runner.py
Runs faster-whisper + pyannote on an already-separated vocals.wav.
Called via: conda run -n sonitr python _whisper_runner.py
"""
import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--vocals",   required=True)
parser.add_argument("--audio",    required=True, help="Original audio (for duration probe)")
parser.add_argument("--job_dir",  required=True)
parser.add_argument("--hf_token", default="")
parser.add_argument("--source_lang",  default="ja")
parser.add_argument("--num_speakers", type=int, default=0)
parser.add_argument("--output",       required=True)
args = parser.parse_args()

import subprocess, json as _json
from faster_whisper import WhisperModel
import torch

vocals_path = Path(args.vocals)
job_dir     = Path(args.job_dir)

# ── Transcription ─────────────────────────────────────────────────────────────
print(f"[Whisper] Loading model large-v3 on {'cuda' if torch.cuda.is_available() else 'cpu'}...", flush=True)
device  = "cuda" if torch.cuda.is_available() else "cpu"
compute = "float16" if device == "cuda" else "int8"
model   = WhisperModel("large-v3", device=device, compute_type=compute)

print("[Whisper] Transcribing (preserving source language)...", flush=True)
raw_segs, info = model.transcribe(
    str(vocals_path),
    task="transcribe",
    language=args.source_lang,
    beam_size=5,
    word_timestamps=False,
)
raw_segs = list(raw_segs)
print(f"[Whisper] Done. {len(raw_segs)} segments. Language: {info.language} ({info.language_probability:.0%})", flush=True)

segments = [
    {
        "id": i,
        "start": seg.start,
        "end": seg.end,
        "text": seg.text.strip(),        # original Japanese
        "translated_text": "",           # filled in by Translate step
        "speaker": "SPEAKER_00",
    }
    for i, seg in enumerate(raw_segs)
    if seg.text.strip()
]

# ── Diarization ───────────────────────────────────────────────────────────────
# Load pre-existing diarization from Step 4 — vocals.wav is at:
#   workspace/2_isolated/{ep}/htdemucs/{ep}/vocals.wav
# so ep_folder = vocals_path.parent.name (the inner {ep} folder)
_ep_folder = vocals_path.parent.name
_yanflix_root = Path(__file__).resolve().parent   # yanflix/
_diar_json = _yanflix_root / "workspace" / "2_isolated" / _ep_folder / "speakers" / "diarization.json"

if _diar_json.exists():
    print(f"[Diarize] Loading pre-existing diarization from Step 4: {_diar_json}", flush=True)
    _diar_data = _json.loads(_diar_json.read_text(encoding="utf-8"))
    _diar_segs = _diar_data.get("segments", [])
    if _diar_segs:
        for seg in segments:
            best, best_overlap = "SPEAKER_00", 0.0
            for ds in _diar_segs:
                overlap = min(seg["end"], ds["end"]) - max(seg["start"], ds["start"])
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = ds["speaker"]
            seg["speaker"] = best
        speakers = sorted(set(s["speaker"] for s in segments))
        print(f"[Diarize] Assigned {len(speakers)} speakers from cached diarization: {', '.join(speakers)}", flush=True)
    else:
        print("[Diarize] diarization.json had no segments — all SPEAKER_00", flush=True)
elif args.hf_token:
    try:
        print("[Pyannote] No cached diarization — running pyannote now...", flush=True)
        from pyannote.audio import Pipeline as DiarizePipeline
        import torch
        original_torch_load = torch.load
        def _patched_load(*a, **kw): kw["weights_only"] = False; return original_torch_load(*a, **kw)
        torch.load = _patched_load
        diarize = DiarizePipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=args.hf_token,
        )
        if torch.cuda.is_available():
            diarize = diarize.to(torch.device("cuda"))
        spk_kwargs = {"num_speakers": args.num_speakers} if args.num_speakers > 0 else {}
        diarization = diarize(str(vocals_path), **spk_kwargs)
        for seg in segments:
            best, best_overlap = "SPEAKER_00", 0.0
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                overlap = min(seg["end"], turn.end) - max(seg["start"], turn.start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = speaker
            seg["speaker"] = best
        speakers = sorted(set(s["speaker"] for s in segments))
        print(f"[Pyannote] Done. Speakers found: {', '.join(speakers)}", flush=True)
    except Exception as e:
        print(f"[Pyannote] Skipped: {e}", flush=True)
else:
    print("[Diarize] No cached diarization and no HF token — all segments assigned SPEAKER_00", flush=True)

# ── Duration probe ────────────────────────────────────────────────────────────
probe = subprocess.run(
    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", args.audio],
    capture_output=True, text=True
)
total_duration = float(_json.loads(probe.stdout).get("format", {}).get("duration", 0))

# ── Sanity check ─────────────────────────────────────────────────────────────
unique_speakers = set(s["speaker"] for s in segments)
print(f"[Check] {len(segments)} segments · {len(unique_speakers)} unique speaker(s): {sorted(unique_speakers)}", flush=True)
if len(unique_speakers) == 1:
    print("[Check] WARNING: only one speaker assigned — diarization may not have loaded correctly.", flush=True)

# ── Write result ──────────────────────────────────────────────────────────────
result = {
    "segments": segments,
    "no_vocals_path": str(job_dir / "no_vocals.wav"),
    "total_duration": total_duration,
}
Path(args.output).write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[Done] {len(segments)} segments -> {args.output}", flush=True)
