"""
run_pipeline.py
Full auto-run: waits for Stage 1 to finish, then runs Stages 2, 3, 4.
Launch separately AFTER _stage1_runner.py is already running.
"""
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# ── Job config ────────────────────────────────────────────────────────────────
JOB_DIR        = BASE_DIR / "jobs" / "smoking_s01e01"
STAGE1_RESULT  = JOB_DIR / "stage1_result.json"
AUDIO_PATH     = BASE_DIR / "workspace" / "1_inputs" / "smoking_supermarket_s01e01.m4a"
OUTPUT_DIR     = BASE_DIR / "output"
SHOW_NAME      = "Smoking Behind the Supermarket with You"
SHOW_SLUG      = "smoking_behind_the_supermarket_with_you"
EPISODE_CONTEXT = (
    "Romance anime. Quiet, bittersweet tone. Two lonely people slowly connecting. "
    "Conversations happen outside a convenience store at night. Understated emotions — "
    "nothing is over-dramatized. Characters speak softly, with pauses."
)

# Speaker → character name (matched to global_roster folder names)
SPEAKER_MAPPINGS = {
    "SPEAKER_00": "dante_basco",
    "SPEAKER_01": "rihanna",
    "SPEAKER_02": "tara_strong",
    "SPEAKER_03": "zeno_robinson",
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# WAIT FOR STAGE 1
# ═══════════════════════════════════════════════════════════════════════════════
log("Waiting for Stage 1 (Demucs + faster-whisper + pyannote) to finish...")
dots = 0
while not STAGE1_RESULT.exists():
    time.sleep(30)
    dots += 1
    log(f"  Still waiting... ({dots * 30}s elapsed)")

log("Stage 1 result found. Loading segments...")
with open(STAGE1_RESULT, encoding="utf-8") as f:
    stage1 = json.load(f)

segments       = stage1["segments"]
no_vocals_path = Path(stage1["no_vocals_path"])
total_duration = stage1["total_duration"]
speakers_found = sorted(set(s.get("speaker", "SPEAKER_00") for s in segments))
log(f"  {len(segments)} segments | {total_duration:.1f}s | speakers: {', '.join(speakers_found)}")


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — DIRECTOR (Ollama emotion tags)
# ═══════════════════════════════════════════════════════════════════════════════
log("Stage 2: Director rewriting script with emotion tags (Ollama llama3.1:8b)...")

from director import apply_emotion_tags

segments = apply_emotion_tags(
    segments=segments,
    show_name=SHOW_NAME,
    episode_context=EPISODE_CONTEXT,
    checkpoint_path=JOB_DIR,
)

with open(JOB_DIR / "state_director.json", "w", encoding="utf-8") as f:
    json.dump(segments, f, ensure_ascii=False, indent=2)

tagged = sum(1 for s in segments if s.get("emotion_line"))
log(f"  {tagged}/{len(segments)} lines tagged. Saved state_director.json.")


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — ACTOR (IndexTTS-1.5)
# ═══════════════════════════════════════════════════════════════════════════════
log("Stage 3: Actor generating voice performances (IndexTTS-1.5)...")

from actor import generate_all_lines

audio_dir = JOB_DIR / "tts_audio"
audio_dir.mkdir(exist_ok=True)

def actor_progress(current, total):
    if current % 10 == 0 or current == total:
        log(f"  Actor: {current}/{total} lines generated")

segments = generate_all_lines(
    transcript_segments=segments,
    speaker_mappings=SPEAKER_MAPPINGS,
    show_slug=SHOW_SLUG,
    output_dir=audio_dir,
    progress_callback=actor_progress,
)

with open(JOB_DIR / "state_actor.json", "w", encoding="utf-8") as f:
    json.dump(segments, f, ensure_ascii=False, indent=2)

generated = sum(1 for s in segments if s.get("audio_path"))
log(f"  {generated}/{len(segments)} voice lines generated. Saved state_actor.json.")


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — SYNC & MUX
# ═══════════════════════════════════════════════════════════════════════════════
log("Stage 4: Syncing, time-stretching, and mixing final audio...")

from sync import run_sync_pipeline

OUTPUT_DIR.mkdir(exist_ok=True)
output_path = OUTPUT_DIR / "Smoking_Behind_the_Supermarket_with_You_S01E01_dubbed.wav"

success = run_sync_pipeline(
    segments=segments,
    job_dir=JOB_DIR,
    video_path=AUDIO_PATH,
    no_vocals_path=no_vocals_path,
    output_path=output_path,
    total_duration=total_duration,
)

if success:
    size_mb = round(output_path.stat().st_size / 1_000_000, 1)
    log(f"\n{'='*60}")
    log(f"  DONE!  {output_path.name}  ({size_mb} MB)")
    log(f"{'='*60}\n")
else:
    log("ERROR: Sync/mux failed. Check job folder for partial outputs.")
    log(f"  Job folder: {JOB_DIR}")
    sys.exit(1)
