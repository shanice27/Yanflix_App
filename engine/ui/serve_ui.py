"""
serve_ui.py — Yanflix local API + static server
Serves Yanflix.html and exposes /api/* endpoints so the UI
can read job state, transcripts, and trigger pipeline stages.

Run: python serve_ui.py
Open: http://localhost:8080/app/Yanflix.html
"""
import http.server
import socketserver
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

PORT = 8080
ROOT = Path(__file__).resolve().parent   # yanflix/
JOBS = ROOT / "jobs"
CHARACTERS = ROOT / "characters"
SHOWS = CHARACTERS / "shows"
GLOBAL_ROSTER = CHARACTERS / "global_roster"



def _json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", len(body))
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            # Client disconnected mid-write (common with aggressive polling).
            pass
    except (ConnectionAbortedError, BrokenPipeError):
        pass


def _latest_job_file(filename: str) -> Optional[Path]:
    """Return the most recently modified matching file across all job folders."""
    candidates = list(JOBS.glob(f"*/{filename}"))
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

def _latest_job_file_in(ep_folder: str, filenames: list) -> Optional[Path]:
    """Return the first existing file from filenames list in the given job folder."""
    job_dir = JOBS / ep_folder
    for name in filenames:
        f = job_dir / name
        if f.exists():
            return f
    return None


class Handler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # ── suppress request log spam ─────────────────────────────────────────────
    def log_message(self, fmt, *args):
        pass

    # ── CORS pre-flight ───────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/checkpoint"):
            self._get_checkpoint()
        elif self.path == "/api/status":
            self._get_status()
        elif self.path == "/api/segments":
            self._get_segments()
        elif self.path == "/api/characters":
            self._get_characters()
        elif self.path == "/api/shows":
            self._get_shows()
        elif self.path.startswith("/api/cast_mapping"):
            self._get_cast_mapping()
        elif self.path.startswith("/api/translate_status"):
            self._get_step_status("translate")
        elif self.path.startswith("/api/direct_status"):
            self._get_step_status("direct")
        elif self.path.startswith("/api/isolate_status"):
            self._get_isolate_status()
        elif self.path.startswith("/api/diarize_status"):
            self._get_diarize_status()
        elif self.path.startswith("/api/clone_status"):
            self._get_clone_status()
        elif self.path.startswith("/api/actor_status"):
            self._get_actor_status()
        elif self.path.startswith("/api/regen_status"):
            self._get_regen_status()
        elif self.path.startswith("/api/regen_line_status"):
            self._get_regen_line_status_handler()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/transcribe":
            self._post_transcribe()
        elif self.path == "/api/isolate":
            self._post_isolate()
        elif self.path == "/api/diarize":
            self._post_diarize()
        elif self.path == "/api/translate":
            self._post_translate()
        elif self.path == "/api/direct":
            self._post_direct()
        elif self.path == "/api/clone_speakers":
            self._post_clone_speakers()
        elif self.path == "/api/regen_speaker":
            self._post_regen_speaker()
        elif self.path == "/api/save_speaker_to_vault":
            self._post_save_speaker_to_vault()
        elif self.path == "/api/save_cast":
            self._post_save_cast()
        elif self.path == "/api/voice_test":
            self._post_voice_test()
        elif self.path == "/api/regen_line":
            self._post_regen_line()
        elif self.path == "/api/actor":
            self._post_actor()
        elif self.path == "/api/ytdlp":
            self._post_ytdlp()
        elif self.path == "/api/save_segments":
            self._post_save_segments()
        else:
            _json_response(self, {"error": "unknown endpoint"}, 404)

    # ── GET /api/checkpoint?ep_folder=<slug> ─────────────────────────────────
    def _get_checkpoint(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]

        if ep_folder:
            # Look only in the specified episode's job folder
            f = _latest_job_file_in(ep_folder, ["state_director.json", "stage1_result.json"])
            if f:
                data = json.loads(f.read_text(encoding="utf-8"))
                segs = data if isinstance(data, list) else data.get("segments", [])
                _json_response(self, segs)
                return
            _json_response(self, {"error": f"no checkpoint for {ep_folder}"}, 404)
            return

        # Fallback: most recently modified across all jobs
        for fname in ("state_director.json", "stage1_result.json"):
            f = _latest_job_file(fname)
            if f:
                data = json.loads(f.read_text(encoding="utf-8"))
                segs = data if isinstance(data, list) else data.get("segments", [])
                _json_response(self, segs)
                return
        _json_response(self, {"error": "no checkpoint found"}, 404)

    # ── GET /api/status ───────────────────────────────────────────────────────
    def _get_status(self):
        status = {
            "stage1_done":   bool(_latest_job_file("stage1_result.json")),
            "stage2_done":   bool(_latest_job_file("state_director.json")),
            "stage3_done":   bool(_latest_job_file("state_actor.json")),
            "output_ready":  any((ROOT / "output").glob("*.wav")) or any((ROOT / "output").glob("*.mp4")),
            "latest_output": None,
        }
        outputs = list((ROOT / "output").glob("*_dubbed.*"))
        if outputs:
            latest = max(outputs, key=lambda p: p.stat().st_mtime)
            status["latest_output"] = latest.name
        _json_response(self, status)

    # ── GET /api/segments — alias for /api/checkpoint ─────────────────────────
    def _get_segments(self):
        self.path = "/api/checkpoint"
        self._get_checkpoint()

    # ── GET /api/characters — list character voice reference files ──
    def _get_characters(self):
        global_dir = GLOBAL_ROSTER
        shows_dir = SHOWS

        def scan_global():
            out = []
            if global_dir.exists():
                for d in sorted([x for x in global_dir.iterdir() if x.is_dir()], key=lambda p: p.name.lower()):
                    ref = d / "avatar_monologue.wav"
                    if ref.exists():
                        out.append({
                            "id": d.name,
                            "name": d.name.replace("_", " ").title(),
                            "domain": "global",
                            "show": "Global Roster",
                            "file": str(ref.relative_to(ROOT)),
                        })
            return out

        def scan_show(show_slug: str):
            out = []
            sd = shows_dir / show_slug
            if not sd.exists():
                return out
            for d in sorted([x for x in sd.iterdir() if x.is_dir()], key=lambda p: p.name.lower()):
                ref = d / "avatar_monologue.wav"
                if ref.exists():
                    entry = {
                        "id": d.name,
                        "name": d.name.replace("_", " ").title(),
                        "domain": "show",
                        "show": show_slug,
                        "showSlug": show_slug,
                        "file": str(ref.relative_to(ROOT)).replace("\\", "/"),
                    }
                    meta_f = d / "meta.json"
                    if meta_f.exists():
                        try:
                            meta = json.loads(meta_f.read_text(encoding="utf-8"))
                            entry["samplePath"] = meta.get("sample_path", "")
                            entry["clonedAt"] = meta.get("cloned_at", "")
                        except Exception:
                            pass
                    out.append(entry)
            return out

        # show slugs available
        show_slugs = []
        if shows_dir.exists():
            show_slugs = sorted([d.name for d in shows_dir.iterdir() if d.is_dir()], key=lambda s: s.lower())

        data = {
            "global": scan_global(),
            "shows": {s: scan_show(s) for s in show_slugs},
            "showSlugs": show_slugs,
        }
        _json_response(self, data)

    # ── GET /api/shows — list available show slugs ──
    def _get_shows(self):
        if not SHOWS.exists():
            _json_response(self, {"showSlugs": []})
            return
        show_slugs = sorted([d.name for d in SHOWS.iterdir() if d.is_dir()], key=lambda s: s.lower())
        _json_response(self, {"showSlugs": show_slugs})


    # ── POST /api/clone_speakers — IndexTTS clone each speaker → Character Vault ─
    def _post_clone_speakers(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        speakers  = body.get("speakers")   # {SPEAKER_00: {sample: "rel/path"}, ...}
        show_slug = body.get("show_slug")
        ep_folder = body.get("ep_folder")

        if not speakers or not show_slug:
            _json_response(self, {"error": "speakers and show_slug required"}, 400)
            return

        status_file = ROOT / "workspace" / "2_isolated" / ep_folder / "speakers" / ".clone_status"

        def run():
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps({"status": "running", "done": [], "total": len(speakers)}), encoding="utf-8")
            # Write speakers JSON to a temp file to avoid shell quoting issues with spaces in paths
            speakers_file = status_file.parent / "_speakers_input.json"
            speakers_file.write_text(json.dumps(speakers), encoding="utf-8")
            runner = ROOT / "_clone_speakers_runner.py"
            # Use shell=True with a quoted command string to handle spaces in paths
            args_str = (
                f'conda run --no-capture-output -n sonitr '
                f'python "{runner}" '
                f'--speakers_json "{speakers_file}" '
                f'--show_slug "{show_slug}" '
                f'--characters_dir "{ROOT / "characters"}" '
                f'--status "{status_file}"'
            )
            subprocess.run(args_str, shell=True, env={**os.environ, "PYTHONUTF8": "1"})

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started"})

    # ── GET /api/clone_status?ep_folder=<slug> ───────────────────────────────
    def _get_clone_status(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        status_file = ROOT / "workspace" / "2_isolated" / ep_folder / "speakers" / ".clone_status"
        if not status_file.exists():
            _json_response(self, {"status": "not_started"})
            return
        _json_response(self, json.loads(status_file.read_text(encoding="utf-8")))

    # ── POST /api/regen_line — re-run IndexTTS on a single line ─────────────────
    def _post_regen_line(self):
        import re as _re
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        ep_folder   = body.get("ep_folder", "").strip()
        line_id     = body.get("line_id")
        show_slug   = body.get("show_slug", "").strip()
        assignments = body.get("assignments", {})
        style       = body.get("style", "standard")
        if not ep_folder or line_id is None:
            _json_response(self, {"error": "ep_folder and line_id required"}, 400); return
        job_dir   = JOBS / ep_folder
        segs_file = job_dir / "state_director.json"
        if not segs_file.exists():
            _json_response(self, {"error": "No directed script"}, 400); return

        emotion_field = "emotion_line" if style == "standard" else "emotion_line_dialect"
        transl_field  = "translated_text" if style == "standard" else "translated_text_dialect"
        tts_dir       = job_dir / ("tts_audio" if style == "standard" else "tts_audio_dialect")
        label         = "Standard" if style == "standard" else "AAVE Dialect"
        regen_status  = job_dir / f".regen_line_{line_id}_{style}_status"

        data     = json.loads(segs_file.read_text(encoding="utf-8"))
        segments = data if isinstance(data, list) else data.get("segments", [])
        seg = next((s for s in segments if s.get("id") == line_id), None)
        if seg is None:
            _json_response(self, {"error": f"Line {line_id} not found"}, 404); return

        raw_line = seg.get(emotion_field) or seg.get(transl_field) or seg.get("text", "")
        text = _re.sub(r"^\[[^\]]+\]\s*", "", raw_line).strip() or raw_line
        out_wav = tts_dir / f"line_{line_id:04d}.wav"
        tts_dir.mkdir(parents=True, exist_ok=True)

        # Crop per-line vocal reference from vocals.wav
        vocals_wav = ROOT / "workspace" / "2_isolated" / ep_folder / "htdemucs" / ep_folder / "vocals.wav"
        ref_voice = None
        tmp_ref = None
        if vocals_wav.exists():
            import tempfile
            start    = seg.get("start", 0)
            duration = max(0.1, seg.get("end", start + 1) - start)
            tmp_ref  = Path(tempfile.mktemp(suffix=f"_ref_{line_id}.wav"))
            crop_cmd = ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration),
                        "-i", str(vocals_wav), "-ar", "24000", "-ac", "1", str(tmp_ref)]
            r = subprocess.run(crop_cmd, capture_output=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and tmp_ref.exists():
                ref_voice = tmp_ref
                print(f"[Regen/{label}] line {line_id} using per-line vocal crop ({start:.2f}→{start+duration:.2f}s)", flush=True)
        if not ref_voice:
            chars_base  = ROOT / "characters"
            speaker_tag = seg.get("speaker", "SPEAKER_00")
            char_id     = assignments.get(speaker_tag, "")
            char_name   = char_id.lower().strip().replace(" ", "_") if char_id else "default_voice"
            show_sample   = chars_base / "shows"        / show_slug / char_name / "avatar_monologue.wav"
            global_sample = chars_base / "global_roster" / char_name / "avatar_monologue.wav"
            ref_voice = show_sample if show_sample.exists() else global_sample if global_sample.exists() else None
        if not ref_voice:
            _json_response(self, {"error": f"No ref voice for line {line_id}"}); return

        runner = ROOT / "_actor_runner.py"
        INDEXTTS_CHECKPOINTS = r"C:\Users\shani\OneDrive\Desktop\IndexTTS2\checkpoints"
        cmd = ["conda", "run", "-n", "sonitr", "python", str(runner),
               "--checkpoints", INDEXTTS_CHECKPOINTS,
               "--text", text, "--prompt", str(ref_voice), "--output", str(out_wav)]

        regen_status.write_text(json.dumps({"status": "running", "line_id": line_id}), encoding="utf-8")

        def run():
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    env={**os.environ, "PYTHONUTF8": "1"})
            if tmp_ref and tmp_ref.exists():
                try: tmp_ref.unlink()
                except: pass
            if result.returncode == 0:
                regen_status.write_text(json.dumps({"status": "done", "line_id": line_id}), encoding="utf-8")
                print(f"[Regen/{label}] line {line_id} done", flush=True)
            else:
                regen_status.write_text(json.dumps({"status": "error", "line_id": line_id, "error": result.stderr[-200:]}), encoding="utf-8")
                print(f"[Regen/{label}] line {line_id} error: {result.stderr[-200:]}", flush=True)

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started", "line_id": line_id})

    # ── GET /api/regen_line_status?ep_folder=X&line_id=Y&style=Z ────────────────
    def _get_regen_line_status_handler(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        line_id   = params.get("line_id",   [None])[0]
        style     = params.get("style",     ["standard"])[0]
        if not ep_folder or line_id is None:
            _json_response(self, {"error": "ep_folder and line_id required"}, 400); return
        f = JOBS / ep_folder / f".regen_line_{line_id}_{style}_status"
        if not f.exists():
            _json_response(self, {"status": "not_started"}); return
        _json_response(self, json.loads(f.read_text(encoding="utf-8")))

    # ── POST /api/actor — run IndexTTS on all directed segments ──────────────────
    def _post_actor(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        ep_folder   = body.get("ep_folder", "").strip()
        show_slug   = body.get("show_slug", "").strip()
        assignments = body.get("assignments", {})
        style       = body.get("style", "standard")   # "standard" or "dialect"
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        job_dir   = JOBS / ep_folder
        segs_file = job_dir / "state_director.json"
        if not segs_file.exists():
            _json_response(self, {"error": "No directed script — run Direct (Ollama) first"}, 400)
            return

        emotion_field = "emotion_line" if style == "standard" else "emotion_line_dialect"
        transl_field  = "translated_text" if style == "standard" else "translated_text_dialect"
        tts_dir       = job_dir / ("tts_audio" if style == "standard" else "tts_audio_dialect")
        status_file   = job_dir / f".actor_{style}_status"
        label         = "Standard" if style == "standard" else "AAVE Dialect"

        # vocals.wav produced by Demucs — used as per-line reference prompt
        vocals_wav = ROOT / "workspace" / "2_isolated" / ep_folder / "htdemucs" / ep_folder / "vocals.wav"

        def _crop_line_audio(seg, tmp_dir):
            """FFmpeg-crop vocals.wav to [start, end] for this segment. Returns Path or None."""
            if not vocals_wav.exists():
                return None
            start = seg.get("start", 0)
            end   = seg.get("end",   start + 1)
            duration = max(0.1, end - start)
            tmp = tmp_dir / f"ref_{seg['id']:04d}.wav"
            crop_cmd = ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration),
                        "-i", str(vocals_wav), "-ar", "24000", "-ac", "1", str(tmp)]
            r = subprocess.run(crop_cmd, capture_output=True, encoding="utf-8", errors="replace")
            return tmp if r.returncode == 0 and tmp.exists() else None

        def run():
            import re, sys, tempfile
            status_file.write_text(json.dumps({"status": "running", "done_lines": [], "style": style}), encoding="utf-8")
            try:
                data     = json.loads(segs_file.read_text(encoding="utf-8"))
                segments = data if isinstance(data, list) else data.get("segments", [])
                tts_dir.mkdir(parents=True, exist_ok=True)
                chars_base = ROOT / "characters"
                done_lines = []
                use_per_line = vocals_wav.exists()
                if use_per_line:
                    print(f"[Actor/{label}] Using per-line vocal crops from {vocals_wav.name}", flush=True)
                else:
                    print(f"[Actor/{label}] vocals.wav not found — falling back to character samples", flush=True)
                tmp_dir = Path(tempfile.mkdtemp(prefix="yanflix_ref_"))
                for seg in segments:
                    line_id     = seg.get("id", 0)
                    speaker_tag = seg.get("speaker", "SPEAKER_00")
                    if seg.get("isSong"):
                        print(f"[Actor/{label}] line {line_id} is a song — skipping TTS", flush=True)
                        continue
                    raw_line = seg.get(emotion_field) or seg.get(transl_field) or seg.get("text", "")
                    text = re.sub(r"^\[[^\]]+\]\s*", "", raw_line).strip() or raw_line
                    if not text:
                        continue
                    # Use per-line vocal crop as prompt; fall back to character sample
                    ref_voice = _crop_line_audio(seg, tmp_dir) if use_per_line else None
                    if not ref_voice:
                        char_id   = assignments.get(speaker_tag, "")
                        char_name = char_id.lower().strip().replace(" ", "_") if char_id else "default_voice"
                        show_sample   = chars_base / "shows"        / show_slug / char_name / "avatar_monologue.wav"
                        global_sample = chars_base / "global_roster" / char_name / "avatar_monologue.wav"
                        ref_voice = show_sample if show_sample.exists() else global_sample if global_sample.exists() else None
                    if not ref_voice:
                        print(f"[Actor/{label}] No ref voice for {speaker_tag}, skipping line {line_id}", flush=True)
                        continue
                    out_wav = tts_dir / f"line_{line_id:04d}.wav"
                    runner  = ROOT / "_actor_runner.py"
                    INDEXTTS_CHECKPOINTS = r"C:\Users\shani\OneDrive\Desktop\IndexTTS2\checkpoints"
                    cmd = ["conda", "run", "-n", "sonitr", "python", str(runner),
                           "--checkpoints", INDEXTTS_CHECKPOINTS,
                           "--text", text, "--prompt", str(ref_voice), "--output", str(out_wav)]
                    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
                    if result.returncode == 0:
                        done_lines.append(line_id)
                        status_file.write_text(json.dumps({"status": "running", "done_lines": done_lines, "style": style}), encoding="utf-8")
                        print(f"[Actor/{label}] line {line_id} done", flush=True)
                    else:
                        print(f"[Actor/{label}] line {line_id} error: {result.stderr[-200:]}", flush=True)
                status_file.write_text(json.dumps({"status": "done", "done_lines": done_lines, "style": style}), encoding="utf-8")
            except Exception as exc:
                import traceback; traceback.print_exc()
                status_file.write_text(json.dumps({"status": "error", "error": str(exc), "style": style}), encoding="utf-8")

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started", "style": style})

    # ── GET /api/actor_status?ep_folder=<slug>&style=standard|dialect ───────────
    def _get_actor_status(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        style     = params.get("style", ["standard"])[0]
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        status_file = JOBS / ep_folder / f".actor_{style}_status"
        if not status_file.exists():
            _json_response(self, {"status": "not_started"})
            return
        _json_response(self, json.loads(status_file.read_text(encoding="utf-8")))

    # ── POST /api/ytdlp — download video via yt-dlp ───────────────────────────────
    def _post_ytdlp(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        url  = body.get("url", "").strip()
        slug = body.get("slug", "video").strip().replace(" ", "_")
        if not url:
            _json_response(self, {"error": "url required"}, 400)
            return
        out_dir = ROOT / "workspace" / "0_raw_videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tmpl = str(out_dir / f"{slug}.%(ext)s")
        cmd = ["yt-dlp", "-o", out_tmpl, "--no-playlist", url]
        def run():
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                print(f"[yt-dlp] Error: {result.stderr[-300:]}", flush=True)
            else:
                print(f"[yt-dlp] Done → {out_dir}", flush=True)
        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started", "filename": f"{slug}.mkv"})

    # ── POST /api/voice_test — run IndexTTS on the avatar monologue for a character ─
    def _post_voice_test(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        ref_path = body.get("ref")    # relative path to avatar_monologue.wav
        char_id  = body.get("char_id", "test")
        if not ref_path:
            _json_response(self, {"error": "ref required"}, 400)
            return
        ref_abs = ROOT / ref_path
        if not ref_abs.exists():
            _json_response(self, {"error": f"Reference not found: {ref_path}"}, 404)
            return

        AVATAR_TEXT = (
            "Water. Earth. Fire. Air. Long ago, the four nations lived together in harmony. "
            "Then, everything changed when the Fire Nation attacked. Only the Avatar, master "
            "of all four elements, could stop them, but when the world needed him most, he vanished."
        )

        out_dir  = ROOT / "workspace" / "voice_tests"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{char_id}_avatar_test.wav"

        runner = ROOT / "_actor_runner.py"
        INDEXTTS_CHECKPOINTS = r"C:\Users\shani\OneDrive\Desktop\IndexTTS2\checkpoints"
        cmd = [
            "conda", "run", "-n", "sonitr",
            "python", str(runner),
            "--checkpoints", INDEXTTS_CHECKPOINTS,
            "--text",   AVATAR_TEXT,
            "--prompt", str(ref_abs),
            "--output", str(out_file),
        ]

        def run():
            env = {**os.environ, "PYTHONUTF8": "1"}
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            if result.returncode != 0:
                print(f"[VoiceTest] Error: {result.stderr[-300:]}", flush=True)
            else:
                print(f"[VoiceTest] Done → {out_file.name}", flush=True)

        threading.Thread(target=run, daemon=True).start()
        out_rel = str(out_file.relative_to(ROOT)).replace("\\", "/")
        _json_response(self, {"status": "started", "out": out_rel})

    # ── POST /api/save_segments — write edited segments back to state_director.json ─
    def _post_save_segments(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        ep_folder = body.get("ep_folder", "").strip()
        segments  = body.get("segments", [])
        if not ep_folder or not segments:
            _json_response(self, {"error": "ep_folder and segments required"}, 400)
            return
        job_dir  = JOBS / ep_folder
        job_dir.mkdir(parents=True, exist_ok=True)
        out_file = job_dir / "state_director.json"
        out_file.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[Segments] Saved {len(segments)} segments → {out_file}", flush=True)
        _json_response(self, {"ok": True, "count": len(segments)})

    # ── GET /api/cast_mapping?ep_folder=<slug> — load saved cast from disk ───────
    def _get_cast_mapping(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        cast_file = JOBS / ep_folder / "cast_mapping.json"
        if not cast_file.exists():
            _json_response(self, {"error": "not found"}, 404)
            return
        _json_response(self, json.loads(cast_file.read_text(encoding="utf-8")))

    # ── POST /api/save_cast — persist speaker→character mapping to disk ──────────
    def _post_save_cast(self):
        import datetime
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        ep_folder   = body.get("ep_folder", "").strip()
        assignments = body.get("assignments", {})   # {SPEAKER_00: "char_id", ...}
        characters  = body.get("characters", {})    # {char_id: {name, notes, ...}}
        show_name   = body.get("show_name", "")
        source_lang = body.get("source_lang", "")
        if not ep_folder or not assignments:
            _json_response(self, {"error": "ep_folder and assignments required"}, 400)
            return
        job_dir = JOBS / ep_folder
        job_dir.mkdir(parents=True, exist_ok=True)
        cast = {
            "ep_folder":   ep_folder,
            "show_name":   show_name,
            "source_lang": source_lang,
            "saved_at":    datetime.datetime.utcnow().isoformat() + "Z",
            "assignments": assignments,
            "characters":  characters,
        }
        cast_file = job_dir / "cast_mapping.json"
        cast_file.write_text(json.dumps(cast, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[Cast] Saved {len(assignments)} speaker mappings → {cast_file}", flush=True)
        _json_response(self, {"ok": True, "path": str(cast_file.relative_to(ROOT)).replace("\\", "/")})

    # ── POST /api/save_speaker_to_vault — name a speaker and save sample to Vault ──
    def _post_save_speaker_to_vault(self):
        import shutil, datetime
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        spk_id    = body.get("spk_id", "").strip()
        name      = body.get("name", "").strip().lower().replace(" ", "_")
        show_slug = body.get("show_slug", "").strip()
        ep_folder = body.get("ep_folder", "").strip()
        print(f"[Vault] save request: spk={spk_id!r} name={name!r} show={show_slug!r} ep={ep_folder!r}", flush=True)
        if not spk_id or not name or not show_slug or not ep_folder:
            _json_response(self, {"error": f"missing fields: spk_id={spk_id!r} name={name!r} show_slug={show_slug!r} ep_folder={ep_folder!r}"}, 400)
            return
        sample_src = ROOT / "workspace" / "2_isolated" / ep_folder / "speakers" / spk_id / "sample.wav"
        if not sample_src.exists():
            _json_response(self, {"error": f"sample.wav not found for {spk_id}"}, 404)
            return
        dest_dir = ROOT / "characters" / "shows" / show_slug / name
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sample_src, dest_dir / "avatar_monologue.wav")
        # Remove the raw SPEAKER_XX folder so it stops appearing in the vault
        old_spk_dir = ROOT / "characters" / "shows" / show_slug / spk_id
        if old_spk_dir.exists() and old_spk_dir != dest_dir:
            shutil.rmtree(old_spk_dir)
            print(f"[Vault] Removed old {spk_id} folder", flush=True)
        meta = {
            "speaker_id": spk_id,
            "show_slug": show_slug,
            "name": name,
            "sample_path": str(sample_src).replace("\\", "/"),
            "cloned_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        (dest_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[Vault] Saved {spk_id} → {name} ({dest_dir})", flush=True)
        _json_response(self, {"ok": True, "name": name, "path": str(dest_dir / "avatar_monologue.wav").replace("\\", "/")})

    # ── POST /api/regen_speaker — re-clone a single speaker from saved meta ──
    def _post_regen_speaker(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        show_slug  = body.get("show_slug")
        speaker_id = body.get("speaker_id")
        if not show_slug or not speaker_id:
            _json_response(self, {"error": "show_slug and speaker_id required"}, 400)
            return

        char_dir  = SHOWS / show_slug / speaker_id
        meta_file = char_dir / "meta.json"
        if not meta_file.exists():
            _json_response(self, {"error": "meta.json not found — cannot regen without original sample path"}, 400)
            return

        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as e:
            _json_response(self, {"error": f"bad meta.json: {e}"}, 400)
            return

        sample_path = meta.get("sample_path", "")
        if not sample_path or not Path(sample_path).exists():
            _json_response(self, {"error": f"sample not found at {sample_path}"}, 400)
            return

        status_file = char_dir / ".regen_status"

        def run():
            status_file.write_text(json.dumps({"status": "running"}), encoding="utf-8")
            speakers = {speaker_id: {"sample": sample_path}}
            speakers_file = char_dir / "_regen_input.json"
            speakers_file.write_text(json.dumps(speakers), encoding="utf-8")
            runner   = ROOT / "_clone_speakers_runner.py"
            args_str = (
                f'conda run --no-capture-output -n sonitr '
                f'python "{runner}" '
                f'--speakers_json "{speakers_file}" '
                f'--show_slug "{show_slug}" '
                f'--characters_dir "{ROOT / "characters"}" '
                f'--status "{status_file}"'
            )
            subprocess.run(args_str, shell=True, env={**os.environ, "PYTHONUTF8": "1"})

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started"})

    # ── GET /api/regen_status?show_slug=X&speaker_id=Y ───────────────────────
    def _get_regen_status(self):
        from urllib.parse import urlparse, parse_qs
        params     = parse_qs(urlparse(self.path).query)
        show_slug  = params.get("show_slug",  [None])[0]
        speaker_id = params.get("speaker_id", [None])[0]
        if not show_slug or not speaker_id:
            _json_response(self, {"error": "show_slug and speaker_id required"}, 400)
            return
        status_file = SHOWS / show_slug / speaker_id / ".regen_status"
        if not status_file.exists():
            _json_response(self, {"status": "not_started"})
            return
        _json_response(self, json.loads(status_file.read_text(encoding="utf-8")))

    # ── POST /api/diarize — run pyannote speaker diarization + extract samples ─
    def _post_diarize(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        vocals           = body.get("vocals")
        ep_folder        = body.get("ep_folder")
        num_speakers     = int(body.get("num_speakers", 0))
        min_speakers     = int(body.get("min_speakers", 0))
        max_speakers     = int(body.get("max_speakers", 0))
        merge_threshold  = float(body.get("merge_threshold", 0.82))

        if not vocals or not ep_folder:
            _json_response(self, {"error": "vocals and ep_folder required"}, 400)
            return

        vocals_path = ROOT / vocals
        if not vocals_path.exists():
            _json_response(self, {"error": f"Vocals file not found: {vocals}"}, 404)
            return

        out_dir     = ROOT / "workspace" / "2_isolated" / ep_folder / "speakers"
        status_file = out_dir / ".diarize_status"

        def run():
            out_dir.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps({"status": "running"}), encoding="utf-8")
            try:
                runner = ROOT / "_diarize_runner.py"
                cmd = [
                    "conda", "run", "--no-capture-output", "-n", "sonitr",
                    "python", str(runner),
                    "--vocals",       str(vocals_path),
                    "--out_dir",      str(out_dir),
                    "--hf_token",     _read_hf_token(),
                    "--status",       str(status_file),
                    "--num_speakers",    str(num_speakers),
                    "--min_speakers",    str(min_speakers),
                    "--max_speakers",    str(max_speakers),
                    "--merge_threshold", str(merge_threshold),
                ]
                subprocess.run(cmd, env={**os.environ, "PYTHONUTF8": "1"})
            except Exception as exc:
                import traceback
                traceback.print_exc()
                status_file.write_text(json.dumps({"status": "error", "error": str(exc)}), encoding="utf-8")

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started"})

    # ── GET /api/diarize_status?ep_folder=<slug> ─────────────────────────────
    def _get_diarize_status(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        status_file = ROOT / "workspace" / "2_isolated" / ep_folder / "speakers" / ".diarize_status"
        if not status_file.exists():
            _json_response(self, {"status": "not_started"})
            return
        _json_response(self, json.loads(status_file.read_text(encoding="utf-8")))

    # ── POST /api/isolate — run Demucs in background, write status file ──────
    def _post_isolate(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        audio     = body.get("audio")       # e.g. workspace/1_inputs/smoking_supermarket_s01e01.m4a
        ep_folder = body.get("ep_folder")   # e.g. smoking_supermarket_s01e01

        if not audio or not ep_folder:
            _json_response(self, {"error": "audio and ep_folder required"}, 400)
            return

        audio_path = ROOT / audio
        if not audio_path.exists():
            _json_response(self, {"error": f"Audio file not found: {audio}"}, 404)
            return

        out_base    = ROOT / "workspace" / "2_isolated" / ep_folder
        status_file = out_base / ".isolate_status"

        def run():
            import shutil
            out_base.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps({"status": "running"}), encoding="utf-8")
            try:
                env = {**os.environ, "PYTHONUTF8": "1"}
                cmd = [
                    "conda", "run", "--no-capture-output", "-n", "sonitr",
                    "demucs", "--two-stems=vocals",
                    "-o", str(out_base),
                    str(audio_path),
                ]
                subprocess.run(cmd, env=env, check=True)

                # Demucs writes: out_base/htdemucs/<track_stem>/vocals.wav + no_vocals.wav
                track_stem = audio_path.stem
                demucs_out = out_base / "htdemucs" / track_stem
                vocals     = demucs_out / "vocals.wav"
                bg         = demucs_out / "no_vocals.wav"

                if not vocals.exists() or not bg.exists():
                    raise FileNotFoundError(f"Demucs output not found in {demucs_out}")

                status_file.write_text(json.dumps({
                    "status":     "done",
                    "vocals":     str(vocals.relative_to(ROOT)).replace("\\", "/"),
                    "background": str(bg.relative_to(ROOT)).replace("\\", "/"),
                }), encoding="utf-8")
                print(f"[isolate] done → {vocals.name}  +  {bg.name}", flush=True)

            except Exception as exc:
                status_file.write_text(json.dumps({"status": "error", "error": str(exc)}), encoding="utf-8")
                print(f"[isolate] ERROR: {exc}", flush=True)

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started"})

    # ── GET /api/isolate_status?ep_folder=<slug> ─────────────────────────────
    def _get_isolate_status(self):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        status_file = ROOT / "workspace" / "2_isolated" / ep_folder / ".isolate_status"
        if not status_file.exists():
            _json_response(self, {"status": "not_started"})
            return
        _json_response(self, json.loads(status_file.read_text(encoding="utf-8")))

    # ── GET /api/translate_status | /api/direct_status ───────────────────────
    def _get_step_status(self, step: str):
        from urllib.parse import urlparse, parse_qs
        params    = parse_qs(urlparse(self.path).query)
        ep_folder = params.get("ep_folder", [None])[0]
        style     = params.get("style", ["standard"])[0]
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        status_file = JOBS / ep_folder / f".{step}_{style}_status"
        if not status_file.exists():
            _json_response(self, {"status": "not_started"})
            return
        _json_response(self, json.loads(status_file.read_text(encoding="utf-8")))

    # ── POST /api/translate — run Ollama translation on existing segments ─────
    def _post_translate(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        ep_folder   = body.get("ep_folder")
        model       = body.get("model", "llama3.1:8b")
        source_lang = body.get("source_lang", "")
        style       = body.get("style", "standard")   # "standard" or "dialect"
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        job_dir   = JOBS / ep_folder
        segs_file = job_dir / "state_director.json"
        if not segs_file.exists():
            segs_file = job_dir / "stage1_result.json"
        if not segs_file.exists():
            _json_response(self, {"error": f"No checkpoint in jobs/{ep_folder} — run Transcribe first"}, 400)
            return
        status_file = job_dir / f".translate_{style}_status"

        def run():
            runner    = ROOT / "_translate_runner.py"
            cast_file = job_dir / "cast_mapping.json"
            lang      = source_lang
            if not lang and cast_file.exists():
                try:
                    lang = json.loads(cast_file.read_text(encoding="utf-8")).get("source_lang", "")
                except Exception:
                    pass
            cmd = ["conda", "run", "--no-capture-output", "-n", "sonitr",
                   "python", str(runner),
                   "--segments", str(segs_file),
                   "--model",    model,
                   "--status",   str(status_file),
                   "--style",    style]
            if cast_file.exists():
                cmd += ["--cast", str(cast_file)]
            if lang:
                cmd += ["--source_lang", lang]
            subprocess.run(cmd, env={**os.environ, "PYTHONUTF8": "1"})

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started", "style": style})

    # ── POST /api/direct — run Ollama director (emotion tags) on segments ─────
    def _post_direct(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        ep_folder = body.get("ep_folder")
        show_name = body.get("show_name", "")
        model     = body.get("model", "llama3.1:8b")
        style     = body.get("style", "standard")   # "standard" or "dialect"
        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return
        job_dir   = JOBS / ep_folder
        segs_file = job_dir / "state_director.json"
        if not segs_file.exists():
            segs_file = job_dir / "stage1_result.json"
        if not segs_file.exists():
            _json_response(self, {"error": f"No checkpoint in jobs/{ep_folder} — run Transcribe first"}, 400)
            return
        status_file = job_dir / f".direct_{style}_status"

        def run():
            runner    = ROOT / "_direct_runner.py"
            cast_file = job_dir / "cast_mapping.json"
            cmd = ["conda", "run", "--no-capture-output", "-n", "sonitr",
                   "python", str(runner),
                   "--segments",  str(segs_file),
                   "--show_name", show_name,
                   "--model",     model,
                   "--status",    str(status_file),
                   "--style",     style]
            if cast_file.exists():
                cmd += ["--cast", str(cast_file)]
            subprocess.run(cmd, env={**os.environ, "PYTHONUTF8": "1"})

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started", "style": style})

    # ── POST /api/transcribe — launch pipeline in background thread ───────────
    def _post_transcribe(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        audio     = body.get("audio", "")
        show      = body.get("show_name", "")
        lang      = body.get("lang", "ja")
        ep_folder = body.get("ep_folder", "").strip()

        if not ep_folder:
            _json_response(self, {"error": "ep_folder required"}, 400)
            return

        # Vocals already isolated by Demucs in Step 2
        vocals_path = ROOT / "workspace" / "2_isolated" / ep_folder / "htdemucs" / ep_folder / "vocals.wav"
        if not vocals_path.exists():
            # fallback: flat layout some Demucs versions use
            vocals_path = ROOT / "workspace" / "2_isolated" / ep_folder / "vocals.wav"

        def run():
            env = {**os.environ, "PYTHONUTF8": "1"}
            job_dir = JOBS / ep_folder          # episode-scoped, never collides
            job_dir.mkdir(parents=True, exist_ok=True)
            result_file = job_dir / "stage1_result.json"

            if not result_file.exists():
                if vocals_path.exists():
                    runner = ROOT / "_whisper_runner.py"
                    cmd = ["conda", "run", "--no-capture-output", "-n", "sonitr", "python", str(runner),
                           "--vocals",      str(vocals_path),
                           "--audio",       audio or str(vocals_path),
                           "--job_dir",     str(job_dir),
                           "--hf_token",    _read_hf_token(),
                           "--source_lang", lang,
                           "--output",      str(result_file)]
                elif audio:
                    runner = ROOT / "_stage1_runner.py"
                    cmd = ["conda", "run", "--no-capture-output", "-n", "sonitr", "python", str(runner),
                           "--audio",       audio,
                           "--job_dir",     str(job_dir),
                           "--hf_token",    _read_hf_token(),
                           "--source_lang", lang,
                           "--output",      str(result_file)]
                else:
                    print(f"[API] No vocals or audio found for {ep_folder}", flush=True)
                    return
                subprocess.run(cmd, env=env)

            if not result_file.exists():
                return  # Stage 1 failed

            # Stage 2 — Director runs AFTER translate, not here.
            # Just copy stage1 into state_director.json so /api/checkpoint can find it.
            stage1 = json.loads(result_file.read_text(encoding="utf-8"))
            segs = stage1["segments"]

            # ── Whisper hallucination guard ───────────────────────────────────
            # Whisper repeats credit/silence phrases dozens of times at the end
            # of audio. Remove any text that appears more than 3 times.
            from collections import Counter as _Counter
            _counts = _Counter(s["text"].strip() for s in segs)
            _hallucinated = {t for t, c in _counts.items() if c > 3}
            if _hallucinated:
                _before = len(segs)
                segs = [s for s in segs if s["text"].strip() not in _hallucinated]
                for _i, _s in enumerate(segs):
                    _s["id"] = _i
                print(f"[API] Whisper dedup: removed {_before - len(segs)} hallucinated segments "
                      f"({len(_hallucinated)} phrase(s) appeared >3x)", flush=True)

            out = job_dir / "state_director.json"
            out.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[API] Transcription done. {len(segs)} segments -> {out}", flush=True)

        threading.Thread(target=run, daemon=True).start()
        _json_response(self, {"status": "started", "message": "Pipeline running in background. Poll /api/status for progress."})

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def guess_type(self, path):
        if str(path).endswith(".wav"):  return "audio/wav"
        if str(path).endswith(".m4a"):  return "audio/mp4"
        return super().guess_type(path)


def _read_hf_token() -> str:
    cfg = ROOT / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8")).get("hf_token", "")
    return ""


with socketserver.TCPServer(("", PORT), Handler) as httpd:
    url = f"http://localhost:{PORT}/app/Yanflix.html"
    print(f"\n  Yanflix UI  ->  {url}")
    print(f"  API endpoints:")
    print(f"    GET  /api/status      - pipeline stage status")
    print(f"    GET  /api/checkpoint  - latest transcript (emotion-tagged)")
    print(f"    POST /api/transcribe  - start transcription + Ollama\n")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    httpd.serve_forever()
