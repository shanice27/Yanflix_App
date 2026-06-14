"""
pipeline.py — Yanflix Job Orchestrator
Runs the full 5-stage dubbing pipeline for one episode.
Called by app.py. Reports progress back via callback.
"""

import json
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

from director import apply_emotion_tags
from actor import generate_all_lines
from sync import run_sync_pipeline


def run_stage1(
    audio_path: Path,
    job_dir: Path,
    hf_token: str = None,
    source_lang: str = "ja",
    whisper_model: str = "large-v3",
) -> tuple[list, Path, float]:
    """
    Native Stage 1:
    1. Demucs — separate vocals from background music
    2. Faster-Whisper — transcribe + translate Japanese → English with timestamps
    3. Pyannote — speaker diarization (who is speaking when)

    Returns (segments, no_vocals_path, total_duration)
    """
    from faster_whisper import WhisperModel
    import torch

    no_vocals_path = job_dir / "no_vocals.wav"
    vocals_path    = job_dir / "vocals.wav"

    # ── 1. VOCAL SEPARATION (Demucs) ──────────────────────────────────────────
    print("[Stage 1] Separating vocals with Demucs...")
    demucs_out = job_dir / "demucs"
    subprocess.run(
        ["python", "-m", "demucs", "--two-stems=vocals",
         "-o", str(demucs_out), str(audio_path)],
        check=True, capture_output=True
    )
    # Demucs outputs to: demucs_out/htdemucs/<stem_name>/{vocals,no_vocals}.wav
    stem_dirs = list((demucs_out / "htdemucs").glob("*/"))
    if stem_dirs:
        src_dir = stem_dirs[0]
        shutil.copy(src_dir / "vocals.wav",    vocals_path)
        shutil.copy(src_dir / "no_vocals.wav", no_vocals_path)
    else:
        # Fallback: use original audio for both
        shutil.copy(audio_path, vocals_path)
        shutil.copy(audio_path, no_vocals_path)

    # ── 2. TRANSCRIPTION + TRANSLATION (faster-whisper) ───────────────────────
    print(f"[Stage 1] Transcribing with faster-whisper ({whisper_model})...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(whisper_model, device=device, compute_type=compute)

    raw_segments, info = model.transcribe(
        str(vocals_path),
        task="transcribe",      # keep original language — Translate step handles ja→en
        language=source_lang,
        beam_size=5,
        word_timestamps=False,
    )
    raw_segments = list(raw_segments)
    print(f"[Stage 1] Detected language: {info.language} ({info.language_probability:.0%})")

    segments = [
        {
            "id": i,
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "translated_text": "",       # filled in by Translate step
            "speaker": "SPEAKER_00",
        }
        for i, seg in enumerate(raw_segments)
        if seg.text.strip()
    ]

    # ── 3. SPEAKER DIARIZATION (pyannote) ─────────────────────────────────────
    if hf_token:
        try:
            print("[Stage 1] Running speaker diarization with pyannote...")
            from pyannote.audio import Pipeline as DiarizePipeline
            diarize = DiarizePipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=hf_token,
            )
            diarization = diarize(str(vocals_path))

            # Map each segment to the speaker with most overlap
            for seg in segments:
                best_speaker = "SPEAKER_00"
                best_overlap = 0.0
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    overlap = min(seg["end"], turn.end) - max(seg["start"], turn.start)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_speaker = speaker.replace("SPEAKER_", "SPEAKER_")
                seg["speaker"] = best_speaker
            print(f"[Stage 1] Diarization complete.")
        except Exception as e:
            print(f"[Stage 1] Diarization skipped: {e}")
    else:
        print("[Stage 1] No HF token — skipping diarization, all lines assigned to SPEAKER_00.")

    # ── Duration ───────────────────────────────────────────────────────────────
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(audio_path)],
        capture_output=True, text=True
    )
    total_duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 0))

    print(f"[Stage 1] Done. {len(segments)} segments, {total_duration:.1f}s total.")
    return segments, no_vocals_path, total_duration


def save_job_state(job_dir: Path, segments: list, stage: str):
    state_file = job_dir / f"state_{stage}.json"
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    print(f"[Pipeline] Saved state: {state_file.name}")


def load_job_state(job_dir: Path, stage: str) -> list | None:
    state_file = job_dir / f"state_{stage}.json"
    if state_file.exists():
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def run_full_pipeline(
    video_path: str,
    show_name: str,
    character_clips: dict,
    gemini_api_key: str,
    hf_token: str = None,
    output_dir: str = "output",
    episode_context: str = "",
    source_lang: str = "ja",
    status_callback=None
) -> str:
    """
    Main entry point. Runs all 5 stages and returns path to final .mp4.

    status_callback: callable(stage_name: str, progress: float, message: str)
    """

    def status(stage, progress, msg):
        print(f"[{stage}] {progress:.0%} — {msg}")
        if status_callback:
            status_callback(stage, progress, msg)

    video_path = Path(video_path)
    output_dir = Path(output_dir)

    # Create job folder named by show + timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in show_name)
    job_dir = Path("jobs") / f"{safe_name}_{timestamp}"
    job_dir.mkdir(parents=True, exist_ok=True)

    from sync import has_video_stream
    ext = "mp4" if has_video_stream(video_path) else "wav"
    output_path = output_dir / f"{safe_name}_{timestamp}_dubbed.{ext}"
    audio_dir = job_dir / "tts_audio"

    status("Stage 1", 0.0, "Starting transcription pipeline...")

    # ── STAGE 1: NATIVE (Demucs + Faster-Whisper + Pyannote) ──
    cached_soni = load_job_state(job_dir, "sonitranslate")
    no_vocals_path = job_dir / "no_vocals.wav"

    if cached_soni and no_vocals_path.exists():
        segments = cached_soni
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True
        )
        total_duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 0))
        status("Stage 1", 1.0, f"Loaded {len(segments)} segments from checkpoint.")
    else:
        segments, no_vocals_path, total_duration = run_stage1(
            video_path, job_dir,
            hf_token=hf_token,
            source_lang=source_lang,
        )
        save_job_state(job_dir, segments, "sonitranslate")
        status("Stage 1", 1.0, f"Transcription complete. {len(segments)} segments, {total_duration:.1f}s total.")

    # ── STAGE 2: DIRECTOR (Emotion Tags via Gemini) ──
    status("Stage 2", 0.0, "Director rewriting script with emotion tags...")

    cached_director = load_job_state(job_dir, "director")
    if cached_director and all("emotion_line" in s for s in cached_director):
        segments = cached_director
        status("Stage 2", 1.0, "Loaded directed script from checkpoint.")
    else:
        segments = apply_emotion_tags(
            segments=segments,
            api_key=gemini_api_key,
            show_name=show_name,
            episode_context=episode_context,
            checkpoint_path=job_dir
        )
        save_job_state(job_dir, segments, "director")
        status("Stage 2", 1.0, f"Director complete. {sum(1 for s in segments if 'emotion_line' in s)} lines tagged.")

    # ── STAGE 3: ACTOR (IndexTTS-1.5) ──
    status("Stage 3", 0.0, "Actor generating voice performances...")

    safe_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in show_name).lower()

    def actor_progress(current, total):
        status("Stage 3", current / total, f"Generated {current}/{total} lines")

    segments = generate_all_lines(
        transcript_segments=segments,
        speaker_mappings=character_clips,
        show_slug=safe_slug,
        output_dir=audio_dir,
        progress_callback=actor_progress,
    )
    save_job_state(job_dir, segments, "actor")

    generated_count = sum(1 for s in segments if s.get("audio_path"))
    status("Stage 3", 1.0, f"Actor complete. {generated_count}/{len(segments)} lines generated.")

    # ── STAGES 4+5: SYNC + MUX ──
    status("Stage 4", 0.0, "Syncing, mixing, and muxing final video...")

    success = run_sync_pipeline(
        segments=segments,
        job_dir=job_dir,
        video_path=video_path,
        no_vocals_path=no_vocals_path,
        output_path=output_path,
        total_duration=total_duration
    )

    if not success:
        raise RuntimeError("Sync/mux pipeline failed. Check job folder for partial outputs.")

    status("Stage 4", 1.0, f"Done! Output: {output_path}")
    return str(output_path)
