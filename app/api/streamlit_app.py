import base64
import csv
import json
import re
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path

import streamlit as st

# ── 1. Page Configuration Force Setup ─────────────────────────────────────────
st.set_page_config(
    page_title="Yanflix — Private AI Dubbing Suite",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Core directory alignment mapping
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure your workspace directories are built and ready
from app.audio_processor import YanflixAudioEngine

audio_engine = YanflixAudioEngine()
PROMPT_PATH = ROOT_DIR / "prompts" / "translation_prompt.txt"
CHARACTERS_ROOT = ROOT_DIR / "characters"
SHOWS_ROOT = CHARACTERS_ROOT / "shows"
SHOWS_ROOT.mkdir(parents=True, exist_ok=True)
CHARACTERS_ROOT.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "project"


def first_non_empty(mapping: dict, keys: list[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def coerce_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return [item.strip() for item in re.split(r"[;,\n]+", str(value)) if item.strip()]


def ingest_csv_profile(csv_text: str, csv_filename: str = "show.csv") -> dict:
    rows = []
    try:
        reader = csv.DictReader(StringIO(csv_text))
        rows = [
            {k: (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader
            if any((v or "").strip() for v in row.values())
        ]
    except Exception:
        rows = []

    if not rows:
        legacy_rows = [
            row for row in csv.reader(StringIO(csv_text)) if any(cell.strip() for cell in row)
        ]
        if len(legacy_rows) > 1:
            headers = [cell.strip() for cell in legacy_rows[0]]
            rows = [
                {headers[index]: (cell.strip() if index < len(cell) else "") for index in range(len(headers))}
                for cell in legacy_rows[1:]
            ]

    title = first_non_empty(rows[0] if rows else {}, ["title", "show title", "series", "show_name", "show", "movie", "name"])
    title = title or Path(csv_filename).stem.replace("_", " ").replace("-", " ")
    format_value = "Series"
    if rows:
        raw_format = first_non_empty(rows[0], ["format", "type", "project format"])
        if raw_format:
            format_value = raw_format
    genres = []
    if rows:
        genres = coerce_list(first_non_empty(rows[0], ["genres", "genre", "categories"]))
    release_year = first_non_empty(rows[0] if rows else {}, ["year", "release year", "release_year"])

    episodes = []
    all_cast = []
    for row in rows:
        episode_name = first_non_empty(row, ["episode name", "episode", "episode title", "title", "name"])
        if not episode_name:
            episode_name = title
        cast_members = coerce_list(first_non_empty(row, ["cast", "cast list", "characters", "character roster", "roster"]))
        if cast_members:
            all_cast.extend(cast_members)
        episodes.append({
            "episode_name": episode_name,
            "cast": cast_members,
        })

    if not episodes:
        episodes = [{"episode_name": title, "cast": []}]

    show_slug = slugify(title)
    show_dir = SHOWS_ROOT / show_slug
    show_dir.mkdir(parents=True, exist_ok=True)

    profile_payload = {
        "title": title,
        "format": format_value,
        "genres": genres,
        "release_year": release_year,
        "episodes": episodes,
        "csv_filename": csv_filename,
        "translation_prompt": str(PROMPT_PATH.relative_to(ROOT_DIR)),
    }
    project_path = audio_engine.dirs["projects"] / f"{show_slug}.json"
    save_json(project_path, profile_payload)

    roster_payload = {
        "show": title,
        "show_slug": show_slug,
        "characters": [],
    }
    for cast_member in sorted(set(all_cast)):
        character_path = show_dir / f"{slugify(cast_member)}.json"
        character_payload = {
            "name": cast_member,
            "show": title,
            "show_slug": show_slug,
            "source": "csv_import",
            "notes": "Imported from CSV batch profile",
        }
        save_json(character_path, character_payload)
        roster_payload["characters"].append({"name": cast_member, "profile": str(character_path.relative_to(ROOT_DIR))})
    save_json(show_dir / "roster.json", roster_payload)

    return {
        "show_title": title,
        "show_slug": show_slug,
        "episodes": len(episodes),
        "characters": len(roster_payload["characters"]),
        "project_path": str(project_path.relative_to(ROOT_DIR)),
    }


# ── 2. Strip Native Streamlit Chrome entirely ─────────────────────────────────
st.markdown(
    """
    <style>
        #MainMenu, header, footer, [data-testid="stHeader"] {
            visibility: hidden;
            display: none !important;
        }
        .stApp, [data-testid="stAppViewContainer"] {
            background-color: #080810 !important;
            overflow: hidden !important;
            height: 100vh !important;
        }
        [data-testid="stSidebar"] {
            display: none !important;
            width: 0px !important;
        }
        [data-testid="stMainBlockContainer"], .main .block-container {
            padding: 0px !important;
            margin: 0px !important;
            max-width: 100% !important;
            width: 100% !important;
            height: 100vh !important;
        }
        div[data-testid="stHtml"] {
            width: 100% !important;
            height: 100vh !important;
        }
        iframe {
            display: block;
            border: none;
            margin: 0;
            padding: 0;
            width: 100vw !important;
            height: 100vh !important;
        }

        /* Eradicate native fallback label boxes breaking through custom form elements */
        div[class*="stFieldVisibilityContainer"],
        div[data-testid="stLabelVisibility"],
        .st-emotion-cache-v0,
        [data-testid="stFormSubmitButton"] {
            display: none !important;
            visibility: hidden !important;
            height: 0px !important;
            margin: 0px !important;
            padding: 0px !important;
            border: none !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── 3. Load UI and Safe Component Channel Communication ───────────────────────
html_file_path = Path(__file__).parent / "Yanflix.html"
logo_path = Path(__file__).parent.parent / "yanflix_logo.svg"

if html_file_path.exists():
    with open(html_file_path, "r", encoding="utf-8") as f:
        html_layout_content = f.read()

    # Base64 Vector Logo injection mapping
    if logo_path.exists():
        with open(logo_path, "r", encoding="utf-8") as f:
            svg_data = f.read()
        b64_svg = base64.b64encode(svg_data.encode("utf-8")).decode("utf-8")
        data_url = f"data:image/svg+xml;base64,{b64_svg}"
        html_layout_content = html_layout_content.replace("yanflix_logo.svg", data_url)

    # ── 4. Receive Incoming Files or Links from the UI Component Channel ─────
    ui_event_data = st.components.v1.html(html_layout_content, height=None, scrolling=True)

    if ui_event_data and isinstance(ui_event_data, dict) and "action" in ui_event_data:
        action_type = ui_event_data["action"]
        meta_title = ui_event_data.get("title") or "Untitled Project"
        meta_format = ui_event_data.get("format") or "Movie"
        meta_genre = ui_event_data.get("genre") or ""
        meta_season_ep = ui_event_data.get("season_ep") or ""

        project_slug = slugify(meta_title) or "project"
        profile_path = audio_engine.dirs["projects"] / f"{project_slug}_profile.json"
        save_json(
            profile_path,
            {
                "title": meta_title,
                "format": meta_format,
                "genre": meta_genre,
                "season_ep": meta_season_ep,
                "translation_prompt": str(PROMPT_PATH.relative_to(ROOT_DIR)),
            },
        )

        if action_type == "local_upload":
            file_name = ui_event_data["filename"]
            raw_base64_bytes = ui_event_data["base64_data"]
            output_destination = audio_engine.dirs["inputs"] / file_name
            with open(output_destination, "wb") as f:
                f.write(base64.b64decode(raw_base64_bytes))

            try:
                audio_engine.separate_audio(output_destination)
                st.toast(f"✨ Local track loaded and separated for {meta_title}!")
            except Exception as e:
                st.error(f"Processing Error: {str(e)}")

        elif action_type == "csv_import":
            csv_text = ui_event_data.get("raw_csv_text", "")
            csv_filename = ui_event_data.get("csv_filename", "show.csv")
            if csv_text:
                try:
                    summary = ingest_csv_profile(csv_text, csv_filename)
                    st.toast(
                        f"📊 {summary['show_title']} profile imported with {summary['episodes']} episode entries and {summary['characters']} character slots."
                    )
                except Exception as e:
                    st.error(f"CSV Import Error: {str(e)}")
            else:
                st.error("No CSV content was received.")

        elif action_type == "video_scrape":
            yt_url = ui_event_data.get("video_url")
            if not yt_url:
                st.error("No video URL was provided.")
            else:
                raw_video_dir = audio_engine.dirs["raw_videos"]
                raw_video_dir.mkdir(parents=True, exist_ok=True)
                output_template = str(raw_video_dir / f"{project_slug}.%(ext)s")
                ytdlp_binary = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
                if not ytdlp_binary:
                    st.error("yt-dlp was not found on PATH. Install it or add it to your environment.")
                else:
                    st.toast(f"📡 yt-dlp is depositing the raw video for {meta_title} into {raw_video_dir}...")
                    try:
                        subprocess.run(
                            [ytdlp_binary, "-f", "bestvideo+bestaudio/best", "-o", output_template, yt_url],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        downloaded_files = list(raw_video_dir.glob(f"{project_slug}.*"))
                        video_files = [
                            p
                            for p in downloaded_files
                            if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".m4a", ".wav", ".mp3", ".avi", ".mov"}
                        ]
                        if video_files:
                            st.toast(f"🎬 Raw video saved in {raw_video_dir} for {meta_title}. Ready for Clipchamp detachment.")
                        else:
                            st.error("yt-dlp completed, but no downloadable media file was found.")
                    except Exception as e:
                        st.error(f"Scraper Error: {str(e)}")
else:
    st.error(f"Layout engine error: Could not verify design file at {html_file_path.absolute()}")