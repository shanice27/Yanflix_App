"""yanflix/actor.py
Actor stage: Voice generation using IndexTTS-1.5 via the 'sonitr' Conda environment.
"""

from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

_BRACKET_TAG = re.compile(r"^\[[^\]]+\]\s*")

INDEXTTS_CHECKPOINTS = r"C:\Users\shani\OneDrive\Desktop\IndexTTS2\checkpoints"
INDEXTTS_DIR = r"C:\Users\shani\OneDrive\Desktop\IndexTTS2"

RUNNER_SCRIPT = Path(__file__).resolve().parent / "_actor_runner.py"

TEXT = (
    "Water. Earth. Fire. Air. Long ago, the four nations lived together in harmony. "
    "Then, everything changed when the Fire Nation attacked. Only the Avatar, master "
    "of all four elements, could stop them, but when the world needed him most, he vanished."
)


def generate_all_lines(
    transcript_segments: list[dict],
    speaker_mappings: dict[str, str],
    show_slug: str,
    output_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """
    Iterates through translated script lines, matches speakers to character references,
    and runs IndexTTS-1.5 inside the 'sonitr' conda environment.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    total_lines = len(transcript_segments)
    characters_base = Path(__file__).resolve().parent / "characters"

    print(f"[Actor] Starting vocal synthesis for {total_lines} lines via IndexTTS-1.5...")

    for idx, seg in enumerate(transcript_segments):
        line_id = seg.get("id", idx)
        speaker_tag = seg.get("speaker", "SPEAKER_00")
        # Strip the [acting tag] prefix — it's a director note, not spoken text
        raw_line = seg.get("emotion_line") or seg.get("translated_text") or seg.get("text", "")
        text = _BRACKET_TAG.sub("", raw_line).strip() or raw_line

        line_wav_path = output_dir / f"line_{line_id:04d}.wav"

        # Resolve character voice reference
        character_name = (
            speaker_mappings.get(speaker_tag, "default_voice")
            .lower().strip().replace(" ", "_")
        )

        show_sample  = characters_base / "shows" / show_slug / character_name / "avatar_monologue.wav"
        global_sample = characters_base / "global_roster" / character_name / "avatar_monologue.wav"
        legacy_sample = characters_base / f"{character_name}.wav"

        if show_sample.exists():
            ref_voice = show_sample
        elif global_sample.exists():
            ref_voice = global_sample
        elif legacy_sample.exists():
            ref_voice = legacy_sample
        else:
            ref_voice = characters_base / "global_roster" / "dante_basco" / "avatar_monologue.wav"

        if not ref_voice.exists():
            print(f"  [Actor] No reference voice for {character_name}, skipping line {line_id}.")
            seg["audio_path"] = None
            if progress_callback:
                progress_callback(idx + 1, total_lines)
            continue

        # Write a self-contained runner script once so conda run can call it as a file
        _ensure_runner_script()

        cmd = [
            "conda", "run", "-n", "sonitr",
            "python",
            str(RUNNER_SCRIPT),
            "--checkpoints", INDEXTTS_CHECKPOINTS,
            "--text", text,
            "--prompt", str(ref_voice),
            "--output", str(line_wav_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env={"PYTHONUTF8": "1", **__import__("os").environ},
            )
            if result.returncode == 0:
                seg["audio_path"] = str(line_wav_path)
            else:
                print(f"  [Actor] Error on line {line_id}:\n{result.stderr[-500:]}")
                seg["audio_path"] = None
        except Exception as e:
            print(f"  [Actor] Exception on line {line_id}: {e}")
            seg["audio_path"] = None

        if progress_callback:
            progress_callback(idx + 1, total_lines)

    return transcript_segments


def _ensure_runner_script():
    """Write _actor_runner.py next to actor.py if it doesn't exist."""
    if RUNNER_SCRIPT.exists():
        return
    RUNNER_SCRIPT.write_text(
        '''"""Thin IndexTTS-1.5 inference wrapper called by actor.py via conda run."""
import argparse, sys
sys.path.insert(0, r"C:\\Users\\shani\\OneDrive\\Desktop\\IndexTTS2")
from indextts.infer import IndexTTS

parser = argparse.ArgumentParser()
parser.add_argument("--checkpoints", required=True)
parser.add_argument("--text", required=True)
parser.add_argument("--prompt", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

tts = IndexTTS(
    cfg_path=f"{args.checkpoints}/config.yaml",
    model_dir=args.checkpoints,
    use_cuda_kernel=False,
)
tts.infer(audio_prompt=args.prompt, text=args.text, output_path=args.output)
''',
        encoding="utf-8",
    )
