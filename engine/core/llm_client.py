"""
llm_client.py — Unified LLM client with fallback chain
  1. Gemini  (GEMINI_API_KEY)
  2. Gemini  (GEMINI_API_KEY_2)
  3. Groq    (GROQ_API_KEY)
  4. Ollama  (localhost:11434)

Usage:
    from llm_client import chat

    text = chat(
        messages=[{"role": "user", "content": "Hello"}],
        system="You are a translator.",
        json_mode=True,
    )
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# ── Load .env.local if keys not already in environment ───────────────────────
def _load_env():
    env = Path(__file__).parent.parent / ".env.local"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── Provider config ───────────────────────────────────────────────────────────
GEMINI_MODEL  = "gemini-2.5-flash"
GROQ_MODEL    = "llama-3.3-70b-versatile"
OLLAMA_MODEL  = "llama3.1:8b"
OLLAMA_URL    = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

_GROQ_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; yanflix/1.0)",
}

# ── Low-level HTTP ────────────────────────────────────────────────────────────
def _post(url, payload, headers, timeout=60):
    data = json.dumps(payload, ensure_ascii=False).encode()
    req  = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── Per-provider callers ──────────────────────────────────────────────────────
def _gemini(api_key, messages, system, json_mode, temperature, max_tokens):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        payload["generationConfig"]["response_mime_type"] = "application/json"

    resp = _post(url, payload, {"Content-Type": "application/json"}, timeout=60)
    return resp["candidates"][0]["content"]["parts"][0]["text"]


def _groq(api_key, messages, system, json_mode, temperature, max_tokens):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    payload = {
        "model": GROQ_MODEL,
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    resp = _post(
        "https://api.groq.com/openai/v1/chat/completions",
        payload,
        {**_GROQ_HEADERS, "Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    return resp["choices"][0]["message"]["content"]


def _ollama(messages, system, json_mode, temperature, max_tokens):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    payload = {
        "model": OLLAMA_MODEL,
        "messages": msgs,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if json_mode:
        payload["format"] = "json"

    resp = _post(f"{OLLAMA_URL}/api/chat", payload, {"Content-Type": "application/json"}, timeout=120)
    return resp["message"]["content"]


# ── Public interface ──────────────────────────────────────────────────────────
def chat(
    messages: list,
    system: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> str:
    """
    Send a chat request through the fallback chain.
    Returns the response text string.
    Raises RuntimeError if all providers fail.
    """
    gemini_key1 = os.environ.get("GEMINI_API_KEY")
    gemini_key2 = os.environ.get("GEMINI_API_KEY_2")
    groq_key    = os.environ.get("GROQ_API_KEY")

    providers = []
    if gemini_key1:
        providers.append(("Gemini-1",  lambda: _gemini(gemini_key1, messages, system, json_mode, temperature, max_tokens)))
    if gemini_key2:
        providers.append(("Gemini-2",  lambda: _gemini(gemini_key2, messages, system, json_mode, temperature, max_tokens)))
    if groq_key:
        providers.append(("Groq",      lambda: _groq(groq_key, messages, system, json_mode, temperature, max_tokens)))
    providers.append(    ("Ollama",    lambda: _ollama(messages, system, json_mode, temperature, max_tokens)))

    last_err = None
    for name, call in providers:
        try:
            result = call()
            if providers[0][0] != name:
                print(f"[llm_client] Used fallback provider: {name}", flush=True)
            return result
        except urllib.error.HTTPError as e:
            # Don't fall through on auth errors — wrong key won't fix itself
            if e.code in (401, 403):
                print(f"[llm_client] {name} auth error ({e.code}) — skipping", flush=True)
            elif e.code == 429:
                print(f"[llm_client] {name} quota/rate limit — trying next", flush=True)
            else:
                print(f"[llm_client] {name} HTTP {e.code} — trying next", flush=True)
            last_err = e
        except Exception as e:
            print(f"[llm_client] {name} failed: {e} — trying next", flush=True)
            last_err = e

    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")
