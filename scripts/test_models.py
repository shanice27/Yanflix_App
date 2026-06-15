import os
import urllib.request
import urllib.error
import json
import base64
import mimetypes
from pathlib import Path

# ── Load .env.local ──────────────────────────────────────────────────────────
def load_env_local():
    env_path = Path(__file__).parent / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

load_env_local()

# ── Colors ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ── Audio file for testing ────────────────────────────────────────────────────
AUDIO_FILE = Path(__file__).parent / "jobs" / "smoking_supermarket_s01e01" / "line_clips" / "line_000.wav"

# ── Model lists ──────────────────────────────────────────────────────────────
GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite-preview-06-17",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

GROQ_CHAT_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "compound-beta",
    "compound-beta-mini",
]

GROQ_AUDIO_MODELS = [
    "whisper-large-v3",
    "whisper-large-v3-turbo",
]

# Rachel — ElevenLabs stock voice (Creator plan required for API access)
ELEVENLABS_TEST_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODELS = [
    "eleven_multilingual_v2",
    "eleven_turbo_v2_5",
    "eleven_flash_v2_5",
]


# ── Testers ──────────────────────────────────────────────────────────────────
def _http_json(url, payload, headers):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as _:
            return "ok", None
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode()
            body = json.loads(raw)
        except Exception:
            body = {}
        # Groq wraps in {"error": {"message": ...}}; fallback to raw text
        err_obj = body.get("error", {})
        msg = (err_obj.get("message") if isinstance(err_obj, dict) else None) or raw or str(e)
        if e.code == 404:
            return "not_found", msg
        if e.code == 429:
            return "quota", msg
        return "error", f"HTTP {e.code}: {msg}"
    except Exception as e:
        return "network", str(e)


def test_gemini_text(api_key, model):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    return _http_json(url, {"contents": [{"parts": [{"text": "Hi"}]}]}, {})


def test_gemini_audio(api_key, model, audio_path):
    """Send a real audio file to Gemini via inline base64."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    mime = mimetypes.guess_type(str(audio_path))[0] or "audio/wav"
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode()
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime, "data": audio_b64}},
                {"text": "Transcribe this audio."},
            ]
        }]
    }
    return _http_json(url, payload, {})


GROQ_HEADERS = {
    "Authorization": "",          # filled in per-call
    "User-Agent": "Mozilla/5.0 (compatible; groq-python/0.11)",
}

def test_groq_chat(api_key, model):
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}
    return _http_json(url, payload, {**GROQ_HEADERS, "Authorization": f"Bearer {api_key}"})


def test_elevenlabs(api_key, model):
    """Synthesize ~5 chars of TTS — cheapest possible real API call."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_TEST_VOICE}"
    payload = {
        "text": "Hi.",
        "model_id": model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _ = r.read(128)  # don't buffer full audio
            return "ok", None
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode()
            body = json.loads(raw)
        except Exception:
            body = {}
        detail = body.get("detail", {})
        msg = (detail.get("message") if isinstance(detail, dict) else str(detail)) or raw or str(e)
        if e.code == 401:
            return "error", f"HTTP 401: invalid API key"
        if e.code == 422:
            return "not_found", msg
        if e.code == 429:
            return "quota", msg
        return "error", f"HTTP {e.code}: {msg}"
    except Exception as e:
        return "network", str(e)


def test_groq_audio(api_key, model, audio_path):
    """Multipart form POST to Groq transcription endpoint."""
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    boundary = "----ModelTestBoundary7A3F"
    audio_bytes = audio_path.read_bytes()
    mime = mimetypes.guess_type(str(audio_path))[0] or "audio/wav"

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{model}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url, data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0 (compatible; groq-python/0.11)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as _:
            return "ok", None
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode()
            body_resp = json.loads(raw)
        except Exception:
            body_resp = {}
        err_obj = body_resp.get("error", {})
        msg = (err_obj.get("message") if isinstance(err_obj, dict) else None) or raw or str(e)
        if e.code == 404:
            return "not_found", msg
        if e.code == 429:
            return "quota", msg
        return "error", f"HTTP {e.code}: {msg}"
    except Exception as e:
        return "network", str(e)


def get_ollama_installed_models(base_url="http://localhost:11434"):
    """Returns (list_of_model_names, error_string_or_None)."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode())
            return [m["name"] for m in data.get("models", [])], None
    except Exception as e:
        err = str(e)
        if "refused" in err.lower():
            return None, "offline"
        return None, err


def test_ollama(model, base_url="http://localhost:11434"):
    # /api/generate is faster than /api/chat for a ping (no prompt processing overhead)
    url = f"{base_url}/api/generate"
    payload = {"model": model, "prompt": "Hi", "stream": False, "options": {"num_predict": 1}}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as _:
            return "ok", None
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode())
        except Exception:
            pass
        msg = body.get("error", str(e))
        if "not found" in msg.lower() or e.code == 404:
            return "not_found", msg
        return "error", f"HTTP {e.code}: {msg}"
    except Exception as e:
        err = str(e)
        if "refused" in err.lower():
            return "offline", "Ollama not running — in sonitr env: conda activate sonitr && ollama serve"
        if "timed out" in err.lower():
            return "error", "timed out after 120s — model may be too large"
        return "network", err


# ── Display helpers ───────────────────────────────────────────────────────────
def print_result(status, msg):
    if status == "ok":
        print(f"{GREEN}✓ works{RESET}")
    elif status == "not_found":
        print(f"{RED}✗ not found{RESET}")
    elif status == "quota":
        print(f"{YELLOW}⚠ quota/rate limit{RESET}")
    elif status == "offline":
        print(f"{YELLOW}⚠ {msg}{RESET}")
    else:
        short = (msg or "")[:80]
        print(f"{RED}✗ {short}{RESET}")


def run_section(title, models, test_fn, key_name=None, key_val=None):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    if key_name:
        masked = (key_val or "")[:8] + "…" if key_val else f"{RED}MISSING{RESET}"
        print(f"  {key_name}: {masked}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}\n")

    ok, failed = [], []
    ollama_offline = False

    for model in models:
        print(f"  {model:<50}", end="", flush=True)

        if ollama_offline:
            print(f"{YELLOW}⚠ skipped (Ollama offline){RESET}")
            failed.append((model, "skipped"))
            continue

        status, msg = test_fn(model)
        print_result(status, msg)

        if status == "ok":
            ok.append(model)
        elif status == "offline":
            ollama_offline = True
            failed.append((model, msg))
        else:
            failed.append((model, msg or status))

    return ok, failed


def print_summary(ok, failed):
    print(f"\n  {BOLD}Results: {GREEN}{len(ok)} working{RESET}{BOLD}, {RED}{len(failed)} unavailable{RESET}")
    if ok:
        print(f"  {GREEN}Working:{RESET} " + ", ".join(ok))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    gemini_keys = [
        ("GEMINI_API_KEY",   os.environ.get("GEMINI_API_KEY")),
        ("GEMINI_API_KEY_2", os.environ.get("GEMINI_API_KEY_2")),
    ]
    groq_key      = os.environ.get("GROQ_API_KEY")
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    ollama_url    = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    audio_ok = AUDIO_FILE.exists()
    audio_label = f"audio: {AUDIO_FILE.name}" if audio_ok else f"{RED}audio file not found: {AUDIO_FILE}{RESET}"

    print(f"\n{BOLD}Model Availability Tester — Gemini · Groq · ElevenLabs · Ollama{RESET}")
    print(f"  Test {audio_label}")

    # ── Gemini text + audio (both keys) ──────────────────────────────────────
    GEMINI_AUDIO_MODELS = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]
    for key_name, key_val in gemini_keys:
        if not key_val:
            print(f"\n{YELLOW}Skipping {key_name} — not set{RESET}")
            continue
        ok, failed = run_section(
            f"GEMINI — TEXT MODELS  [{key_name}]", GEMINI_MODELS,
            lambda m, k=key_val: test_gemini_text(k, m),
            key_name, key_val,
        )
        print_summary(ok, failed)

        if audio_ok:
            ok, failed = run_section(
                f"GEMINI — AUDIO  [{key_name}]  ({AUDIO_FILE.name})",
                GEMINI_AUDIO_MODELS,
                lambda m, k=key_val: test_gemini_audio(k, m, AUDIO_FILE),
                key_name, key_val,
            )
            print_summary(ok, failed)
        else:
            print(f"\n{YELLOW}Skipping Gemini audio — test file not found{RESET}")

    # ── Groq chat models ──────────────────────────────────────────────────────
    if groq_key:
        ok, failed = run_section(
            "GROQ — CHAT MODELS", GROQ_CHAT_MODELS,
            lambda m: test_groq_chat(groq_key, m),
            "GROQ_API_KEY", groq_key,
        )
        print_summary(ok, failed)

        # Groq audio / Whisper
        if audio_ok:
            ok, failed = run_section(
                f"GROQ — AUDIO / WHISPER  ({AUDIO_FILE.name})",
                GROQ_AUDIO_MODELS,
                lambda m: test_groq_audio(groq_key, m, AUDIO_FILE),
                "GROQ_API_KEY", groq_key,
            )
            print_summary(ok, failed)
        else:
            print(f"\n{YELLOW}Skipping Groq audio — test file not found{RESET}")
    else:
        print(f"\n{YELLOW}Skipping Groq — GROQ_API_KEY not set{RESET}")

    # ── ElevenLabs TTS ───────────────────────────────────────────────────────
    if elevenlabs_key:
        ok, failed = run_section(
            "ELEVENLABS — TTS MODELS  (3 chars billed)", ELEVENLABS_MODELS,
            lambda m: test_elevenlabs(elevenlabs_key, m),
            "ELEVENLABS_API_KEY", elevenlabs_key,
        )
        print_summary(ok, failed)
    else:
        print(f"\n{YELLOW}Skipping ElevenLabs — ELEVENLABS_API_KEY not set{RESET}")

    # ── Ollama ────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  OLLAMA MODELS  ({ollama_url}){RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}\n")

    installed, err = get_ollama_installed_models(ollama_url)
    if err == "offline":
        print(f"  {YELLOW}⚠ Ollama not running — in sonitr env: conda activate sonitr && ollama serve{RESET}")
    elif err:
        print(f"  {RED}✗ Could not connect: {err}{RESET}")
    elif not installed:
        print(f"  {YELLOW}⚠ Ollama is running but no models are installed.{RESET}")
        print(f"  Install one with:  ollama pull llama3.2")
    else:
        print(f"  Found {len(installed)} installed model(s)\n")
        ok, failed = [], []
        for model in installed:
            print(f"  {model:<50}", end="", flush=True)
            status, msg = test_ollama(model, ollama_url)
            print_result(status, msg)
            if status == "ok":
                ok.append(model)
            else:
                failed.append((model, msg or status))
        print_summary(ok, failed)

    print()


if __name__ == "__main__":
    main()
