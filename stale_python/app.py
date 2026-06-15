"""
streamlit_app.py — Yanflix Main UI
Launch with: streamlit run streamlit_app.py
"""

import json
import os
import subprocess
import streamlit as st
from pathlib import Path
from datetime import datetime, time as dtime
import shutil

# ── Page Config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Yanflix",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

CONFIG_FILE  = Path("config.json")
CHARACTERS_DIR = Path("characters")
JOBS_DIR     = Path("jobs")
OUTPUT_DIR   = Path("output")
QUEUE_FILE   = Path("jobs/queue.json")

# New explicit workflow destinations for your manual step-by-step processing
PROMPTS_DIR  = Path("system_prompts")
MOVIES_DIR   = Path("movies")
THEME_CSS     = Path(__file__).with_name("theme.css")

# Loop through and instantly build the layout on your storage drive
for d in [CHARACTERS_DIR, JOBS_DIR, OUTPUT_DIR, PROMPTS_DIR, MOVIES_DIR]:
    d.mkdir(exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"gemini_api_key": "", "hf_token": "", "output_dir": "output"}

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_all_characters() -> dict:
    chars = {p.stem: p for p in CHARACTERS_DIR.glob("*.wav")}
    global_roster = CHARACTERS_DIR / "global_roster"
    for char_dir in global_roster.iterdir():
        if char_dir.is_dir():
            ref = char_dir / "avatar_monologue.wav"
            if ref.exists():
                chars[char_dir.name] = ref
    return chars

def load_queue() -> list:
    if QUEUE_FILE.exists():
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return []

def save_queue(queue: list):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)

def check_tool(cmd):
    try:
        subprocess.run(cmd, capture_output=True, timeout=3)
        return True
    except Exception:
        return False


def load_theme_css() -> str:
    if not THEME_CSS.exists():
        return ""
    with open(THEME_CSS, encoding="utf-8") as f:
        return f.read()


st.markdown(f"<style>{load_theme_css()}</style>", unsafe_allow_html=True)

LANG_OPTIONS = {
    "Japanese": "ja", "Korean": "ko", "Mandarin Chinese": "zh",
    "Spanish": "es", "French": "fr", "Portuguese": "pt",
    "German": "de", "Italian": "it", "Arabic": "ar"
}


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎬 Yanflix")
    st.caption("Private AI Dubbing Suite")
    st.divider()
    page = st.radio(
        "Navigate",
        ["🎬 Dub Studio", "🎭 Character Vault", "📚 Library", "⚙️ Settings"],
        label_visibility="collapsed"
    )
    st.divider()
    cfg_check = load_config()
    st.caption("System")
    st.write("FFmpeg", "✅" if check_tool(["ffmpeg", "-version"]) else "❌")
    st.write("Demucs", "✅" if check_tool(["python", "-c", "import demucs"]) else "❌")
    st.write("Gemini Key", "✅ Set" if cfg_check.get("gemini_api_key") else "❌ Missing")
    st.write("HF Token",   "✅ Set" if cfg_check.get("hf_token") else "❌ Missing")


# ══════════════════════════════════════════════════════════════════════════════
# ── SETTINGS ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
if page == "⚙️ Settings":
    st.header("⚙️ Settings")
    cfg = load_config()

    with st.form("settings_form"):
        st.subheader("🔑 API Keys")
        col1, col2 = st.columns(2)
        with col1:
            gemini_key = st.text_input(
                "Gemini API Key",
                value=cfg.get("gemini_api_key", ""),
                type="password",
                help="Free at aistudio.google.com"
            )
        with col2:
            hf_token = st.text_input(
                "Hugging Face Read Token",
                value=cfg.get("hf_token", ""),
                type="password",
                help="huggingface.co/settings/tokens"
            )

        st.subheader("📁 Paths")
        col1, col2 = st.columns(2)
        with col1:
            output_dir = st.text_input("Output Directory", value=cfg.get("output_dir", "output"))
        with col2:
            soni_path = st.text_input(
                "SoniTranslate Path",
                value=cfg.get("soni_path", ""),
                placeholder="e.g. C:/SoniTranslate or leave blank to auto-detect"
            )

        st.subheader("🌐 Language Defaults")
        col1, col2 = st.columns(2)
        with col1:
            default_src = st.selectbox(
                "Default Source Language",
                list(LANG_OPTIONS.keys()),
                index=0,
                help="Can be overridden per job"
            )
        with col2:
            default_tgt = st.selectbox("Default Target Language", ["English"], index=0)

        st.subheader("🔊 Voice Engine")
        fish_mode = st.radio(
            "Fish Speech Mode",
            ["Hugging Face Spaces (default)", "Local server (http://localhost:7860)"],
            help="Switch to Local after you run Fish Speech on your machine for faster, more reliable processing"
        )
        fish_space = st.text_input(
            "HF Space ID or Local URL",
            value=cfg.get("fish_space", "fishaudio/fish-speech-1"),
            help="Only change if the Fish Speech Space URL has changed"
        )

        if st.form_submit_button("💾 Save Settings", use_container_width=True, type="primary"):
            save_config({
                "gemini_api_key": gemini_key,
                "hf_token": hf_token,
                "output_dir": output_dir,
                "soni_path": soni_path,
                "default_src_lang": LANG_OPTIONS[default_src],
                "default_tgt_lang": "en",
                "fish_mode": fish_mode,
                "fish_space": fish_space
            })
            st.success("Settings saved.")

    st.divider()
    st.subheader("🖥️ Full System Status")
    tools = {
        "FFmpeg": ["ffmpeg", "-version"],
        "Demucs": ["python", "-c", "import demucs"],
        "Whisper": ["python", "-c", "import faster_whisper"],
        "pyannote": ["python", "-c", "import pyannote.audio"],
        "gradio_client": ["python", "-c", "import gradio_client"],
        "google-generativeai": ["python", "-c", "import google.generativeai"],
        "librosa": ["python", "-c", "import librosa"],
    }
    cols = st.columns(4)
    for i, (name, cmd) in enumerate(tools.items()):
        with cols[i % 4]:
            ok = check_tool(cmd)
            st.metric(name, "✅ Ready" if ok else "❌ Missing")


# ══════════════════════════════════════════════════════════════════════════════
# ── CHARACTER VAULT ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎭 Character Vault":
    st.header("🎭 Character Vault")
    st.caption("Add a 10-second reference clip per character once. Reuse across every episode forever.")

    characters = get_all_characters()

    if characters:
        st.subheader(f"Saved Characters ({len(characters)})")
        cols = st.columns(3)
        for i, (name, clip_path) in enumerate(characters.items()):
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"**{name}**")
                    st.audio(str(clip_path))
                    col_a, col_b = st.columns(2)
                    with col_a:
                        new_clip = st.file_uploader("Replace clip", type=["wav","mp3"], key=f"rep_{name}", label_visibility="collapsed")
                        if new_clip:
                            with open(clip_path, "wb") as f:
                                f.write(new_clip.read())
                            st.rerun()
                    with col_b:
                        if st.button("🗑️ Delete", key=f"del_{name}", use_container_width=True):
                            clip_path.unlink()
                            st.rerun()
    else:
        st.info("No characters yet. Add one below.")

    st.divider()
    st.subheader("➕ Add New Character")
    with st.form("add_character"):
        col1, col2 = st.columns(2)
        with col1:
            char_name = st.text_input("Character Name", placeholder="e.g. Goku")
        with col2:
            char_show = st.text_input("Show (optional)", placeholder="e.g. Dragon Ball Z")
        ref_clip = st.file_uploader("Reference Audio Clip — 10 seconds, clean dialogue, no BGM", type=["wav","mp3","m4a"])
        char_notes = st.text_area("Notes (optional)", placeholder="e.g. Deep voice, tends to shout. Hero archetype.", height=80)

        if st.form_submit_button("➕ Add Character", use_container_width=True):
            if not char_name or not ref_clip:
                st.error("Name and audio clip are required.")
            else:
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in char_name)
                dest = CHARACTERS_DIR / f"{safe}.wav"
                with open(dest, "wb") as f:
                    f.write(ref_clip.read())
                st.success(f"✅ {char_name} added to vault.")
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ── LIBRARY (Cinematic Netflix Style Grid) ────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📚 Library":
    st.header("📚 Yanflix Home Theater")
    st.caption("Stream your customized redubbed library directly inside your local computer framework.")

    output_dir = Path(load_config().get("output_dir", "output"))
    completed  = sorted(output_dir.glob("*.mp4"), reverse=True)

    if not completed:
        st.info("No completed dubs found in your output folder yet. Run a pipeline to generate media!")
    else:
        st.subheader("📺 Now Playing")
        
        # Maps user-friendly titles to the true file targets
        video_titles = {mp4.stem.replace("_", " "): mp4 for mp4 in completed}
        selected_title = st.selectbox("Select Media to Stream", list(video_titles.keys()), label_visibility="collapsed")
        selected_mp4 = video_titles[selected_title]

        # Standard clean video player anchor
        with st.container(border=True):
            with open(selected_mp4, "rb") as video_file:
                st.video(video_file.read())
            
            meta_col1, meta_col2 = st.columns(2)
            with meta_col1:
                size_mb = selected_mp4.stat().st_size / 1_000_000
                st.markdown(f"**Active Stream:** `{selected_mp4.name}`")
                st.caption(f"File Weight: {size_mb:.1f} MB")
            with meta_col2:
                mtime = datetime.fromtimestamp(selected_mp4.stat().st_mtime).strftime("%b %d, %Y at %I:%M %p")
                st.markdown(f"**Render Date:** {mtime}")
                if st.button("🗑️ Permanently Delete Episode", type="secondary", use_container_width=True):
                    selected_mp4.unlink()
                    st.rerun()

        st.divider()
        st.subheader("🍿 Media Shelf")
        
        # Build out a 4-column layout matrix mimicking your HTML setup
        grid_cols = st.columns(4)
        for idx, mp4 in enumerate(completed):
            col_target = grid_cols[idx % 4]
            with col_target:
                card_title = mp4.stem.replace("_", " ")
                first_letter = card_title[0].upper() if card_title else "M"
                
                # Directly injecting your layout style directly into the runtime view
                html_poster = f"""
                <div style="
                    background: linear-gradient(135deg, #1b1b2c 0%, #0d0d18 100%);
                    border: 1px solid #232336;
                    border-radius: 6px;
                    padding: 40px 15px;
                    text-align: center;
                    font-family: 'Outfit', sans-serif;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                    margin-bottom: 10px;
                 border-left: 3px solid #c8102e;
                ">
                    <div style="font-size: 44px; font-weight: bold; color: #d4af37; opacity: 0.25; font-family: 'Bebas Neue', sans-serif;">{first_letter}</div>
                    <div style="color: #ece9e0; font-size: 13.5px; font-weight: 500; margin-top: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: 0.04em;">{card_title}</div>
                </div>
                """
                st.components.v1.html(html_poster, height=160)

# ══════════════════════════════════════════════════════════════════════════════
# ── DUB STUDIO ────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎬 Dub Studio":
    st.header("🎬 Dub Studio")

    cfg        = load_config()
    characters = get_all_characters()
    char_names = list(characters.keys())

    if not cfg.get("hf_token"):
        st.info("ℹ️ No HuggingFace token set — speaker diarization will be skipped (all lines tagged SPEAKER_00). Add one in **Settings** for multi-speaker separation.")

    if not characters:
        st.warning("⚠️ Go to **Character Vault** and add at least one voice reference before dubbing.")
        st.stop()

    # ── Studio Tabs ────────────────────────────────────────────────────────────
    tab_video, tab_cast, tab_transcript, tab_processing, tab_schedule, tab_run = st.tabs([
        "📹 Video & Episode",
        "🎭 Cast",
        "📝 Transcript",
        "⚙️ Processing",
        "🗓️ Schedule",
        "▶️ Run & Monitor"
    ])

    # ── Session state init ─────────────────────────────────────────────────────
    defaults = {
        "studio_show_name": "",
        "studio_season": 1,
        "studio_episode": 1,
        "studio_source_lang": cfg.get("default_src_lang", "ja"),
        "studio_context": "",
        "studio_video_path": None,
        "studio_assignments": {},
        "studio_num_speakers": 3,
        "studio_segments": [],
        "studio_dub_vol": 1.0,
        "studio_bg_vol": 0.85,
        "studio_tts_retries": 4,
        "studio_gemini_chunk": 20,
        "studio_stretch_min": 0.6,
        "studio_stretch_max": 1.8,
        "studio_nvenc": True,
        "studio_skip_demucs": False,
        "studio_scheduled_time": None,
        "studio_schedule_enabled": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — VIDEO & EPISODE
    # ══════════════════════════════════════════════════════════════════════════
    with tab_video:
        st.subheader("Episode Info")
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.session_state.studio_show_name = st.text_input(
                "Show Name", value=st.session_state.studio_show_name,
                placeholder="e.g. Demon Slayer"
            )
        with col2:
            st.session_state.studio_season = st.number_input(
                "Season", min_value=1, max_value=50,
                value=st.session_state.studio_season
            )
        with col3:
            st.session_state.studio_episode = st.number_input(
                "Episode", min_value=1, max_value=999,
                value=st.session_state.studio_episode
            )

        col1, col2 = st.columns(2)
        with col1:
            lang_display = {v: k for k, v in LANG_OPTIONS.items()}
            current_lang_name = lang_display.get(st.session_state.studio_source_lang, "Japanese")
            chosen_lang = st.selectbox(
                "Source Language",
                list(LANG_OPTIONS.keys()),
                index=list(LANG_OPTIONS.keys()).index(current_lang_name)
            )
            st.session_state.studio_source_lang = LANG_OPTIONS[chosen_lang]
        with col2:
            st.selectbox("Target Language", ["English"], disabled=True,
                         help="English only in this version")

        st.session_state.studio_context = st.text_area(
            "Director's Context Notes",
            value=st.session_state.studio_context,
            placeholder=(
                "Describe the tone and situation so the Director AI writes better emotion tags.\n\n"
                "Example: 'Season finale. Main character is dying. Best friend is in denial. "
                "Villain is calm and satisfied. High emotional stakes throughout.'"
            ),
            height=120
        )

        st.divider()
        st.subheader("Video File")

        uploaded = st.file_uploader(
            "Upload your episode", type=["mp4", "mkv", "avi", "mov"],
            help="MKV files from torrents work fine. The app handles all format conversion."
        )

        if uploaded:
            temp_path = JOBS_DIR / uploaded.name
            if not temp_path.exists():
                with st.spinner("Saving file..."):
                    with open(temp_path, "wb") as f:
                        f.write(uploaded.read())
            st.session_state.studio_video_path = str(temp_path)

            # Video preview and metadata
            col1, col2 = st.columns([2, 1])
            with col1:
                st.video(str(temp_path))
            with col2:
                try:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json",
                         "-show_format", "-show_streams", str(temp_path)],
                        capture_output=True, text=True
                    )
                    meta = json.loads(probe.stdout)
                    fmt = meta.get("format", {})
                    duration_s = float(fmt.get("duration", 0))
                    size_mb = int(fmt.get("size", 0)) / 1_000_000
                    mins, secs = divmod(int(duration_s), 60)

                    st.metric("Duration", f"{mins}m {secs}s")
                    st.metric("File Size", f"{size_mb:.1f} MB")
                    st.metric("Format", fmt.get("format_long_name", "Unknown").split(",")[0])

                    # Count audio/video streams
                    streams = meta.get("streams", [])
                    vid_streams = [s for s in streams if s["codec_type"] == "video"]
                    aud_streams = [s for s in streams if s["codec_type"] == "audio"]
                    if vid_streams:
                        v = vid_streams[0]
                        st.metric("Resolution", f"{v.get('width','?')}×{v.get('height','?')}")
                    st.metric("Audio Tracks", len(aud_streams))
                except Exception:
                    st.info("Could not read video metadata.")

        elif st.session_state.studio_video_path:
            st.success(f"✅ File loaded: `{Path(st.session_state.studio_video_path).name}`")


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — CAST
    # ══════════════════════════════════════════════════════════════════════════
    with tab_cast:
        st.subheader("Speaker → Character Assignments")
        st.caption(
            "The diarization system labels each speaker as SPEAKER_00, SPEAKER_01, etc. "
            "Assign each one to a character voice from your vault."
        )

        col1, col2 = st.columns([1, 3])
        with col1:
            st.session_state.studio_num_speakers = st.number_input(
                "Number of speaking characters in this episode",
                min_value=1, max_value=15,
                value=st.session_state.studio_num_speakers
            )

        st.divider()
        assignments = {}
        num = int(st.session_state.studio_num_speakers)

        # Render assignment rows — 2 per row
        for row_start in range(0, num, 2):
            cols = st.columns(2)
            for col_idx in range(2):
                spk_idx = row_start + col_idx
                if spk_idx >= num:
                    break
                speaker_id = f"SPEAKER_{spk_idx:02d}"
                with cols[col_idx]:
                    with st.container(border=True):
                        top_col, right_col = st.columns([2, 1])
                        with top_col:
                            selected = st.selectbox(
                                f"🎙️ {speaker_id}",
                                options=char_names,
                                index=min(spk_idx, len(char_names) - 1),
                                key=f"cast_spk_{spk_idx}"
                            )
                            assignments[speaker_id] = str(characters[selected])

                        with right_col:
                            # Preview the assigned voice clip
                            clip_path = characters[selected]
                            st.caption("Voice preview")
                            st.audio(str(clip_path))

        st.session_state.studio_assignments = assignments

        if assignments:
            st.divider()
            st.caption(f"✅ {len(assignments)} speaker(s) assigned. Proceed to Transcript or Run & Monitor.")

        st.divider()
        with st.expander("➕ Add a new character without leaving the Studio"):
            with st.form("quick_add_char"):
                qc1, qc2 = st.columns(2)
                with qc1:
                    qname = st.text_input("Character Name")
                with qc2:
                    qclip = st.file_uploader("Reference Clip", type=["wav","mp3","m4a"])
                if st.form_submit_button("Add Character"):
                    if qname and qclip:
                        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in qname)
                        dest = CHARACTERS_DIR / f"{safe}.wav"
                        with open(dest, "wb") as f:
                            f.write(qclip.read())
                        st.success(f"Added {qname}. Refresh this tab to see them in the assignment list.")
                    else:
                        st.error("Name and clip required.")


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3 — TRANSCRIPT
    # ══════════════════════════════════════════════════════════════════════════
    with tab_transcript:
        st.subheader("Transcript Viewer & Script Editor")

        job_slug = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in st.session_state.studio_show_name or "untitled"
        )
        job_latest_dir    = JOBS_DIR / f"{job_slug}_latest"
        soni_checkpoint   = job_latest_dir / "state_sonitranslate.json"
        director_checkpoint = job_latest_dir / "state_director.json"

        # ── TRANSCRIBE BUTTON ──────────────────────────────────────────────────
        st.markdown("#### 🎙️ Transcribe & Analyze")

        _STAGE1_RUNNER = (
            Path(__file__).resolve().parent.parent / "_stage1_runner.py"
        )

        if not st.session_state.studio_video_path:
            st.info("Upload an audio or video file in **Video & Episode** first, then come back here.")
        else:
            tc1, tc2 = st.columns([4, 1])
            with tc1:
                st.caption(
                    f"Ready to transcribe: `{Path(st.session_state.studio_video_path).name}` — "
                    "this runs Demucs (vocal separation) + faster-whisper (transcription) "
                    "+ Ollama (emotion tags). Takes 2–10 min depending on episode length."
                )
            with tc2:
                do_transcribe = st.button(
                    "▶ Transcribe", type="primary", use_container_width=True,
                    key="btn_transcribe"
                )

            if do_transcribe:
                job_latest_dir.mkdir(parents=True, exist_ok=True)
                stage1_result_file = job_latest_dir / "stage1_result.json"
                _env = {**os.environ, "PYTHONUTF8": "1"}

                with st.status("Running Stage 1 — transcription pipeline…", expanded=True) as _status:
                    st.write("Step 1/3 — Demucs: separating vocals from background audio…")
                    st.write("Step 2/3 — faster-whisper: transcribing and translating…")
                    st.write("Step 3/3 — pyannote: identifying speakers…")
                    st.write("*(This may take several minutes — do not close this tab.)*")

                    _proc = subprocess.run(
                        [
                            "conda", "run", "--no-capture-output", "-n", "sonitr",
                            "python", str(_STAGE1_RUNNER),
                            "--audio",       st.session_state.studio_video_path,
                            "--job_dir",     str(job_latest_dir),
                            "--hf_token",    cfg.get("hf_token", ""),
                            "--source_lang", st.session_state.studio_source_lang,
                            "--output",      str(stage1_result_file),
                        ],
                        capture_output=True, text=True, env=_env,
                    )

                    if _proc.returncode != 0:
                        _status.update(label="Stage 1 failed", state="error")
                        st.error("Transcription failed. Check the error below:")
                        st.code(_proc.stderr[-2000:], language=None)
                        st.stop()

                    with open(stage1_result_file, encoding="utf-8") as _f:
                        _stage1 = json.load(_f)
                    _segs = _stage1["segments"]
                    st.write(f"Transcription done — {len(_segs)} lines. Now running Ollama director…")

                    # Stage 2: emotion tags via Ollama (runs in current env, only needs requests)
                    import sys as _sys
                    _yanflix_dir = str(Path(__file__).resolve().parent.parent)
                    if _yanflix_dir not in _sys.path:
                        _sys.path.insert(0, _yanflix_dir)
                    from director import apply_emotion_tags
                    _segs_tagged = apply_emotion_tags(
                        segments=_segs,
                        show_name=st.session_state.studio_show_name,
                        episode_context=st.session_state.studio_context,
                        checkpoint_path=job_latest_dir,
                    )

                    # Persist to checkpoints and session state
                    with open(soni_checkpoint, "w", encoding="utf-8") as _f:
                        json.dump(_segs, _f, ensure_ascii=False, indent=2)
                    with open(director_checkpoint, "w", encoding="utf-8") as _f:
                        json.dump(_segs_tagged, _f, ensure_ascii=False, indent=2)

                    st.session_state.studio_segments = _segs_tagged
                    _status.update(
                        label=f"Done! {len(_segs_tagged)} lines transcribed and analyzed.",
                        state="complete",
                    )

                st.rerun()

        st.divider()

        # ── LOAD FROM CHECKPOINT ───────────────────────────────────────────────
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🔄 Load Transcript from Last Checkpoint", use_container_width=True):
                if soni_checkpoint.exists():
                    with open(soni_checkpoint) as f:
                        st.session_state.studio_segments = json.load(f)
                    if director_checkpoint.exists():
                        with open(director_checkpoint) as f:
                            st.session_state.studio_segments = json.load(f)
                    st.success(f"Loaded {len(st.session_state.studio_segments)} segments.")
                else:
                    st.warning("No checkpoint found. Use the Transcribe button above first.")

        segments = st.session_state.studio_segments

        if segments:
            with col2:
                # Export as SRT
                def build_srt(segs, field="emotion_line"):
                    lines = []
                    for i, s in enumerate(segs):
                        start = s.get("start", 0)
                        end   = s.get("end", 0)
                        def fmt_ts(t):
                            h = int(t // 3600)
                            m = int((t % 3600) // 60)
                            sc = t % 60
                            return f"{h:02d}:{m:02d}:{sc:06.3f}".replace(".", ",")
                        text = s.get(field, s.get("translated_text", s.get("text", "")))
                        lines.append(f"{i+1}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{text}\n")
                    return "\n".join(lines)

                export_field = "emotion_line" if any("emotion_line" in s for s in segments) else "translated_text"
                srt_data = build_srt(segments, export_field)
                st.download_button(
                    "⬇️ Export as .SRT",
                    srt_data,
                    file_name=f"{job_slug}_dubbed.srt",
                    mime="text/plain",
                    use_container_width=True
                )
            with col3:
                json_data = json.dumps(segments, ensure_ascii=False, indent=2)
                st.download_button(
                    "⬇️ Export as .JSON",
                    json_data,
                    file_name=f"{job_slug}_segments.json",
                    mime="application/json",
                    use_container_width=True
                )

            st.divider()

            # Filters
            f1, f2, f3 = st.columns(3)
            with f1:
                filter_speaker = st.selectbox(
                    "Filter by Speaker",
                    ["All"] + sorted(set(s.get("speaker","Unknown") for s in segments))
                )
            with f2:
                show_raw      = st.checkbox("Show Raw Transcript", value=True)
            with f3:
                show_emotion  = st.checkbox("Show Emotion Tags", value=True)

            filtered = segments if filter_speaker == "All" else [
                s for s in segments if s.get("speaker") == filter_speaker
            ]

            st.caption(f"Showing {len(filtered)} of {len(segments)} lines")
            st.divider()

            # Line editor
            edited_segments = list(segments)  # copy to edit

            for i, seg in enumerate(filtered):
                real_idx = segments.index(seg)
                start    = seg.get("start", 0)
                end      = seg.get("end", 0)
                speaker  = seg.get("speaker", "UNKNOWN")
                raw      = seg.get("text", "")
                translated = seg.get("translated_text", "")
                emotion    = seg.get("emotion_line", "")

                char_assigned = ""
                for spk_id, clip_path in st.session_state.studio_assignments.items():
                    if spk_id == speaker:
                        char_assigned = Path(clip_path).stem
                        break

                with st.container(border=True):
                    header_col, time_col = st.columns([3, 1])
                    with header_col:
                        st.markdown(
                            f"**Line {real_idx+1}** — "
                            f"`{speaker}`"
                            + (f" → **{char_assigned}**" if char_assigned else " *(unassigned)*")
                        )
                    with time_col:
                        mins_s, secs_s = divmod(start, 60)
                        mins_e, secs_e = divmod(end, 60)
                        st.caption(f"⏱ {int(mins_s)}:{secs_s:05.2f} → {int(mins_e)}:{secs_e:05.2f}")

                    if show_raw and raw:
                        st.caption(f"📡 Raw: *{raw}*")
                    if translated:
                        st.caption(f"🌐 Translated: {translated}")

                    if show_emotion:
                        new_emotion = st.text_input(
                            "✏️ Emotion-tagged line (editable)",
                            value=emotion,
                            key=f"edit_emotion_{real_idx}",
                            label_visibility="collapsed",
                            placeholder="[emotion tag] dialogue here"
                        )
                        if new_emotion != emotion:
                            edited_segments[real_idx]["emotion_line"] = new_emotion

            if st.button("💾 Save Edited Script", use_container_width=True, type="primary"):
                st.session_state.studio_segments = edited_segments
                # Write back to checkpoint
                if director_checkpoint.parent.exists():
                    with open(director_checkpoint, "w") as f:
                        json.dump(edited_segments, f, ensure_ascii=False, indent=2)
                st.success("Script saved. These edits will be used when the Actor runs.")

        else:
            st.info(
                "No transcript loaded yet. Either:\n"
                "- Run Stage 1 in **Run & Monitor** and then come back here to load it, or\n"
                "- Run the full pipeline and load the checkpoint afterward to review."
            )


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 4 — PROCESSING SETTINGS
    # ══════════════════════════════════════════════════════════════════════════
    with tab_processing:
        st.subheader("Processing Settings")
        st.caption("Fine-tune how each stage runs. Defaults work well for most anime.")

        st.markdown("#### 🔊 Audio Mix")
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.studio_dub_vol = st.slider(
                "Dub Track Volume",
                min_value=0.5, max_value=1.5, step=0.05,
                value=st.session_state.studio_dub_vol,
                help="Volume of your generated voice track in the final mix"
            )
        with col2:
            st.session_state.studio_bg_vol = st.slider(
                "Background (Music/SFX) Volume",
                min_value=0.3, max_value=1.2, step=0.05,
                value=st.session_state.studio_bg_vol,
                help="Volume of the isolated background audio (no vocals) in the final mix"
            )

        st.divider()
        st.markdown("#### 🎙️ Voice Generation (Fish Speech)")
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.studio_tts_retries = st.number_input(
                "TTS Retries per Line",
                min_value=1, max_value=8,
                value=st.session_state.studio_tts_retries,
                help="How many times to retry a failed TTS call before skipping the line"
            )
        with col2:
            st.session_state.studio_gemini_chunk = st.number_input(
                "Gemini Batch Size (lines per API call)",
                min_value=5, max_value=50,
                value=st.session_state.studio_gemini_chunk,
                help="Larger = fewer API calls but higher failure risk. 20 is optimal."
            )

        st.divider()
        st.markdown("#### ⏱️ Lip-Sync / Time Stretch")
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.studio_stretch_min = st.slider(
                "Minimum Stretch Ratio",
                min_value=0.5, max_value=1.0, step=0.05,
                value=st.session_state.studio_stretch_min,
                help="How slow a line can be stretched. 0.6 = 40% slower than TTS output."
            )
        with col2:
            st.session_state.studio_stretch_max = st.slider(
                "Maximum Compression Ratio",
                min_value=1.0, max_value=2.0, step=0.05,
                value=st.session_state.studio_stretch_max,
                help="How fast a line can be compressed. 1.8 = 80% faster than TTS output."
            )
        st.caption(
            "⚠️ Lines that fall outside these limits won't be time-stretched — "
            "they'll play at natural TTS speed instead. Raise limits cautiously."
        )

        st.divider()
        st.markdown("#### 🖥️ Hardware & Output")
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.studio_nvenc = st.toggle(
                "Use NVENC (RTX 4050 Hardware Encoding)",
                value=st.session_state.studio_nvenc,
                help="Faster and cooler than CPU encoding. Auto-falls back to libx264 if unavailable."
            )
        with col2:
            st.session_state.studio_skip_demucs = st.toggle(
                "Skip Vocal Separation (Demucs)",
                value=st.session_state.studio_skip_demucs,
                help="Use only if audio is already pre-separated or you want a faster test run."
            )

        if st.session_state.studio_skip_demucs:
            st.warning(
                "⚠️ Skipping Demucs means the original Japanese voice track will be audible "
                "underneath your dub. Only use this for testing purposes."
            )


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 5 — SCHEDULE
    # ══════════════════════════════════════════════════════════════════════════
    with tab_schedule:
        st.subheader("Job Schedule & Queue")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### ⏰ Schedule This Job")
            st.session_state.studio_schedule_enabled = st.toggle(
                "Schedule for later",
                value=st.session_state.studio_schedule_enabled,
                help="Leave off to run immediately when you click Start"
            )

            if st.session_state.studio_schedule_enabled:
                sched_date = st.date_input("Date", value=datetime.today())
                sched_time = st.time_input(
                    "Start Time",
                    value=dtime(2, 0),
                    help="Set to overnight hours to run while you sleep"
                )
                st.session_state.studio_scheduled_time = datetime.combine(sched_date, sched_time).isoformat()
                st.info(f"🗓️ This job will start at **{sched_date.strftime('%b %d')} at {sched_time.strftime('%I:%M %p')}**")
            else:
                st.session_state.studio_scheduled_time = None
                st.caption("Job will start immediately when you click ▶️ Start in Run & Monitor.")

        with col2:
            st.markdown("#### 📋 Queue Multiple Episodes")
            st.caption(
                "Add this configured job to the queue instead of running it now. "
                "Run the full queue from here in sequence."
            )

            if st.button("➕ Add Current Job to Queue", use_container_width=True):
                if not st.session_state.studio_show_name:
                    st.error("Set a Show Name in the Video & Episode tab first.")
                elif not st.session_state.studio_video_path:
                    st.error("Upload a video file in the Video & Episode tab first.")
                else:
                    queue = load_queue()
                    new_job = {
                        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
                        "show_name": st.session_state.studio_show_name,
                        "season": st.session_state.studio_season,
                        "episode": st.session_state.studio_episode,
                        "video_path": st.session_state.studio_video_path,
                        "source_lang": st.session_state.studio_source_lang,
                        "context": st.session_state.studio_context,
                        "assignments": st.session_state.studio_assignments,
                        "scheduled_time": st.session_state.studio_scheduled_time,
                        "status": "queued"
                    }
                    queue.append(new_job)
                    save_queue(queue)
                    st.success(f"Added to queue: {new_job['show_name']} S{new_job['season']:02d}E{new_job['episode']:02d}")

        st.divider()
        st.markdown("#### 📋 Current Queue")

        queue = load_queue()
        if not queue:
            st.info("Queue is empty. Add jobs above or just run them directly.")
        else:
            for i, job in enumerate(queue):
                status_color = {"queued": "🟡", "running": "🔵", "done": "✅", "failed": "❌"}.get(job["status"], "⚪")
                with st.container(border=True):
                    jc1, jc2, jc3 = st.columns([4, 2, 1])
                    with jc1:
                        label = f"{job['show_name']} S{job.get('season',1):02d}E{job.get('episode',1):02d}"
                        st.markdown(f"{status_color} **{label}** — {job['status'].upper()}")
                        if job.get("scheduled_time"):
                            sched_dt = datetime.fromisoformat(job["scheduled_time"])
                            st.caption(f"Scheduled: {sched_dt.strftime('%b %d at %I:%M %p')}")
                    with jc2:
                        st.caption(f"Source: {job.get('source_lang','?').upper()} → EN")
                    with jc3:
                        if st.button("🗑️", key=f"q_del_{i}", help="Remove from queue"):
                            queue.pop(i)
                            save_queue(queue)
                            st.rerun()

            st.divider()
            if st.button("▶️ Run Entire Queue Now", use_container_width=True, type="primary"):
                st.info("Queue runner not yet implemented in this version. Run jobs individually from Run & Monitor.")


    # ══════════════════════════════════════════════════════════════════════════
    # TAB 6 — RUN & MONITOR
    # ══════════════════════════════════════════════════════════════════════════
    with tab_run:
        st.subheader("Run & Monitor")

        # Pre-flight checklist
        checks = {
            "Show name set":   bool(st.session_state.studio_show_name),
            "Video uploaded":  bool(st.session_state.studio_video_path),
            "Cast assigned":   bool(st.session_state.studio_assignments),
            "Transcript ready": bool(st.session_state.studio_segments),
        }

        st.markdown("#### ✅ Pre-flight Checklist")
        all_clear = all(checks.values())
        check_cols = st.columns(len(checks))
        for col, (label, ok) in zip(check_cols, checks.items()):
            with col:
                st.metric(label, "✅" if ok else "❌")

        if not all_clear:
            st.warning("Complete all checklist items before starting. "
                       "Check the **Video & Episode** and **Cast** tabs.")

        # Job summary
        if st.session_state.studio_show_name or st.session_state.studio_video_path:
            st.divider()
            st.markdown("#### 📋 Job Summary")
            sc1, sc2, sc3 = st.columns(3)
            with sc1:
                st.write(f"**Show:** {st.session_state.studio_show_name or '—'}")
                st.write(f"**Episode:** S{st.session_state.studio_season:02d}E{st.session_state.studio_episode:02d}")
            with sc2:
                src_name = {v:k for k,v in LANG_OPTIONS.items()}.get(st.session_state.studio_source_lang, "?")
                st.write(f"**Source Language:** {src_name}")
                st.write(f"**Speakers:** {st.session_state.studio_num_speakers}")
            with sc3:
                st.write(f"**NVENC:** {'On' if st.session_state.studio_nvenc else 'Off'}")
                st.write(f"**Skip Demucs:** {'Yes ⚠️' if st.session_state.studio_skip_demucs else 'No'}")

        st.divider()

        # Start button
        start_disabled = not all_clear
        if st.button(
            "🚀 Start Full Dub Pipeline",
            disabled=start_disabled,
            use_container_width=True,
            type="primary"
        ):
            from pipeline import run_full_pipeline

            st.divider()
            st.markdown("#### ⚙️ Pipeline Progress")

            stages = {
                "Stage 1": "📡 SoniTranslate — Transcribing, translating & separating vocals",
                "Stage 2": "🎬 Director — Rewriting script with emotion tags",
                "Stage 3": "🎙️ Actor — Generating voice performances",
                "Stage 4": "🎞️ Sync & Mux — Assembling final video",
            }

            stage_ui = {}
            for key, label in stages.items():
                stage_ui[key] = st.empty()
                stage_ui[key].info(f"⏳ {label}")

            progress_bar    = st.progress(0)
            result_area     = st.empty()
            log_expander    = st.expander("📄 Live Log", expanded=True)
            log_placeholder = log_expander.empty()
            log_lines       = []
            stage_order     = list(stages.keys())

            def status_callback(stage, progress, message):
                key = stage.split(":")[0].strip()
                label = stages.get(key, key)
                if progress >= 1.0:
                    stage_ui[key].success(f"✅ {label}")
                elif progress > 0:
                    stage_ui[key].info(f"🔄 {label} — {progress:.0%}")

                # Overall progress
                try:
                    stage_num = stage_order.index(key)
                    overall   = (stage_num + progress) / len(stage_order)
                    progress_bar.progress(min(overall, 1.0))
                except ValueError:
                    pass

                ts = datetime.now().strftime("%H:%M:%S")
                log_lines.append(f"[{ts}] [{key}] {message}")
                log_placeholder.code("\n".join(log_lines[-40:]), language=None)

            try:
                output_path = run_full_pipeline(
                    video_path      = st.session_state.studio_video_path,
                    show_name       = st.session_state.studio_show_name,
                    character_clips = st.session_state.studio_assignments,
                    gemini_api_key  = cfg.get("gemini_api_key", ""),
                    hf_token        = cfg.get("hf_token", ""),
                    output_dir      = cfg.get("output_dir", "output"),
                    episode_context = st.session_state.studio_context,
                    source_lang     = st.session_state.studio_source_lang,
                    status_callback = status_callback
                )

                progress_bar.progress(1.0)
                result_area.success(f"🎉 Dub complete! Saved to: `{output_path}`")

                with open(output_path, "rb") as f:
                    st.download_button(
                        "⬇️ Download Dubbed Episode",
                        f,
                        file_name=Path(output_path).name,
                        mime="video/mp4",
                        use_container_width=True
                    )

            except Exception as e:
                result_area.error(f"❌ Pipeline failed: {e}")
                st.exception(e)
