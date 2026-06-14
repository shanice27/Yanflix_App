"""
sync.py — Yanflix Audio Sync & Muxer
1. Time-stretches each TTS clip to match the original segment duration (atempo).
2. Assembles all clips into one dub track.
3. Mixes dub track with no_vocals.wav background.
4. Final mux into output .mp4 using NVENC.
"""

import subprocess
import json
from pathlib import Path


def get_audio_duration(filepath: Path) -> float:
    """Returns duration of an audio file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(filepath)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream.get("duration", 0))
    return 0.0


def time_stretch_clip(input_wav: Path, target_duration: float, output_wav: Path) -> bool:
    """
    Stretches or compresses input_wav to match target_duration using FFmpeg atempo.
    atempo filter only accepts values between 0.5 and 2.0, so we chain filters for extreme ratios.
    """
    source_duration = get_audio_duration(input_wav)
    if source_duration <= 0:
        return False

    ratio = source_duration / target_duration

    # atempo only accepts 0.5–2.0 per filter; chain multiple for extreme ratios
    filters = []
    r = ratio
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r /= 0.5
    filters.append(f"atempo={r:.4f}")
    atempo_filter = ",".join(filters)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_wav),
        "-filter:a", atempo_filter,
        "-ar", "44100",
        str(output_wav)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def build_silence(duration: float, output_wav: Path, sample_rate: int = 44100) -> bool:
    """Generates a silent .wav of exact duration."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=stereo",
        "-t", str(duration),
        str(output_wav)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def assemble_dub_track(
    segments: list,
    synced_dir: Path,
    output_track: Path,
    total_duration: float
) -> bool:
    """
    Places each time-stretched clip at its correct timestamp in a full-length audio track.
    Uses FFmpeg amix/adelay approach for precise placement.
    """
    synced_dir = Path(synced_dir)
    filter_parts = []
    inputs = []
    valid_segs = [s for s in segments if s.get("audio_path") and "synced_path" in s]

    if not valid_segs:
        print("[Sync] No valid synced segments to assemble.")
        return False

    for i, seg in enumerate(valid_segs):
        delay_ms = int(seg["start"] * 1000)
        inputs += ["-i", seg["synced_path"]]
        filter_parts.append(f"[{i}]adelay={delay_ms}|{delay_ms}[a{i}]")

    mix_inputs = "".join(f"[a{i}]" for i in range(len(valid_segs)))
    filter_parts.append(f"{mix_inputs}amix=inputs={len(valid_segs)}:normalize=0[out]")

    filter_complex = ";".join(filter_parts)

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", str(total_duration),
            "-ar", "44100",
            "-ac", "2",
            str(output_track)
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[Sync] Assembly failed: {result.stderr[-500:]}")
    return result.returncode == 0


def mix_with_background(
    dub_track: Path,
    no_vocals_track: Path,
    output_mix: Path,
    dub_volume: float = 1.0,
    bg_volume: float = 0.9
) -> bool:
    """Mixes dub audio with background music/SFX track."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(dub_track),
        "-i", str(no_vocals_track),
        "-filter_complex",
        f"[0]volume={dub_volume}[dub];[1]volume={bg_volume}[bg];[dub][bg]amix=inputs=2:normalize=0[out]",
        "-map", "[out]",
        "-ar", "44100",
        "-ac", "2",
        str(output_mix)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def final_mux(
    video_path: Path,
    audio_mix: Path,
    output_path: Path,
    use_nvenc: bool = True
) -> bool:
    """
    Muxes final dubbed audio into video using NVENC hardware encoding.
    Falls back to libx264 if NVENC is unavailable.
    """
    video_codec = "h264_nvenc" if use_nvenc else "libx264"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_mix),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", video_codec,
        "-preset", "p4" if use_nvenc else "medium",
        "-cq", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if use_nvenc:
            print("[Sync] NVENC failed, falling back to libx264...")
            return final_mux(video_path, audio_mix, output_path, use_nvenc=False)
        print(f"[Sync] Final mux failed: {result.stderr[-500:]}")
    return result.returncode == 0


def has_video_stream(filepath: Path) -> bool:
    """Returns True if the file contains a video stream."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(filepath)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    streams = json.loads(result.stdout).get("streams", [])
    return any(s.get("codec_type") == "video" for s in streams)


def run_sync_pipeline(
    segments: list,
    job_dir: Path,
    video_path: Path,
    no_vocals_path: Path,
    output_path: Path,
    total_duration: float
) -> bool:
    """
    Full sync pipeline:
    1. Time-stretch each TTS clip to match segment duration
    2. Assemble full dub track with correct timing
    3. Mix with background audio
    4. Final mux to output .mp4
    """
    job_dir = Path(job_dir)
    synced_dir = job_dir / "synced"
    synced_dir.mkdir(exist_ok=True)

    print("[Sync] Step 1: Time-stretching TTS clips...")
    for idx, seg in enumerate(segments):
        if not seg.get("audio_path"):
            continue

        synced_path = synced_dir / f"synced_{idx:04d}.wav"
        if synced_path.exists():
            seg["synced_path"] = str(synced_path)
            continue

        target_duration = seg.get("end", 0) - seg.get("start", 0)
        if target_duration <= 0.1:
            continue

        success = time_stretch_clip(Path(seg["audio_path"]), target_duration, synced_path)
        if success:
            seg["synced_path"] = str(synced_path)
            print(f"[Sync] Stretched line {idx+1} to {target_duration:.2f}s")

    print("[Sync] Step 2: Assembling dub track...")
    dub_track = job_dir / "dub_track.wav"
    if not assemble_dub_track(segments, synced_dir, dub_track, total_duration):
        return False

    print("[Sync] Step 3: Mixing with background audio...")
    final_audio = job_dir / "final_audio.wav"
    if not mix_with_background(dub_track, no_vocals_path, final_audio):
        return False

    if not has_video_stream(video_path):
        print("[Sync] Audio-only source — skipping video mux, writing final audio track...")
        import shutil
        output_path = output_path.with_suffix(".wav")
        shutil.copy(final_audio, output_path)
        print(f"[Sync] Done. Output: {output_path}")
        return True

    print("[Sync] Step 4: Final mux to MP4...")
    return final_mux(video_path, final_audio, output_path)
