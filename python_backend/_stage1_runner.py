"""
_stage1_runner.py
Called via: conda run -n sonitr python _stage1_runner.py --audio ... --job_dir ... --output ...
Runs Demucs + faster-whisper + pyannote inside the sonitr environment.
"""
import argparse
import json
import sys
from pathlib import Path

_YANFLIX_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_YANFLIX_DIR))

from pipeline import run_stage1

parser = argparse.ArgumentParser()
parser.add_argument("--audio",       required=True,  help="Input audio/video file")
parser.add_argument("--job_dir",     required=True,  help="Job working directory")
parser.add_argument("--hf_token",    default="",     help="HuggingFace token for diarization")
parser.add_argument("--source_lang", default="ja",   help="Source language code")
parser.add_argument("--output",      required=True,  help="Path to write segments JSON")
args = parser.parse_args()

segments, no_vocals_path, total_duration = run_stage1(
    audio_path=Path(args.audio),
    job_dir=Path(args.job_dir),
    hf_token=args.hf_token or None,
    source_lang=args.source_lang,
)

result = {
    "segments": segments,
    "no_vocals_path": str(no_vocals_path),
    "total_duration": total_duration,
}
Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[Stage1Runner] Done. {len(segments)} segments written.")
