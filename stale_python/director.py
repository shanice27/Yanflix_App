"""
director.py — Yanflix Contextual Director
Intercepts translated segments and rewrites them
with emotion/acting tags via local Ollama LLM.
"""

import json
import time
import requests
from collections import Counter
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"


DIRECTOR_SYSTEM_PROMPT = """You are the lead dubbing director for a professional English dubbing studio working on animated and live-action foreign-language content.
Your job is to take a batch of translated dialogue lines and rewrite them to be:
1. Natural spoken American English (not literal translation)
2. Emotionally actable by a voice actor
3. Approximately the same syllable count as the original (for lip-sync)

For EVERY line, you MUST prepend an acting tag in square brackets.
Acting tags describe HOW the line should be performed.

Examples of acting tags:
[breathy whisper], [shouting with rage], [uncontrollable sobbing], [cold and threatening],
[nervous and stuttering], [warm and sincere], [mocking laughter], [exhausted and defeated],
[surprised gasp then speaking], [quiet intensity], [cheerful and energetic], [trembling voice]

Rules:
- Never remove or add lines. Input has N lines, output must have exactly N lines.
- Every line starts with [acting tag] then the dialogue.
- Keep names, attack names, and proper nouns unchanged.
- Output ONLY valid JSON. No markdown, no explanation, no backticks.

Output format:
{"lines": ["[acting tag] dialogue here", "[acting tag] dialogue here", ...]}
"""


def chunk_segments(segments: list, chunk_size: int = 20) -> list:
    return [segments[i:i + chunk_size] for i in range(0, len(segments), chunk_size)]


def build_user_prompt(segments: list, show_name: str = "", episode_context: str = "", cast_map: dict = None) -> str:
    context_block = ""
    if show_name:
        context_block += f"Show: {show_name}\n"
    if cast_map:
        context_block += "Characters in this scene:\n"
        for spk, name in sorted(cast_map.items()):
            context_block += f"  {spk} = {name}\n"
    if episode_context:
        context_block += f"Scene context: {episode_context}\n"
    lines_block = "\n".join(
        f"{i+1}. [{seg.get('character_name') or seg.get('speaker', 'Unknown')}]: {seg.get('translated_text', seg.get('text', ''))}"
        for i, seg in enumerate(segments)
    )
    return f"{context_block}\nDialogue lines to rewrite:\n{lines_block}"


def _ollama_generate(prompt: str, model: str, retries: int) -> str:
    """Call Ollama chat endpoint and return the response text."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
    }
    for attempt in range(retries):
        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"[Director] Ollama attempt {attempt+1} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def apply_emotion_tags(
    segments: list,
    api_key: str = None,
    show_name: str = "",
    episode_context: str = "",
    checkpoint_path: Path = None,
    retries: int = 3,
    ollama_model: str = "llama3.1:8b",
    cast_map: dict = None,
    checkpoint_filename: str = "director_checkpoint.json",
) -> list:
    """
    Takes translated segments, returns same segments with 'emotion_line' field added.
    Saves checkpoint after each chunk if checkpoint_path is provided.
    api_key is unused (kept for pipeline compatibility).
    """

    checkpoint_file = None
    completed_indices = set()
    if checkpoint_path:
        checkpoint_file = Path(checkpoint_path) / checkpoint_filename
        if checkpoint_file.exists():
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for item in saved:
                idx = item.get("original_index")
                if idx is not None and "emotion_line" in item:
                    completed_indices.add(idx)
                    segments[idx]["emotion_line"] = item["emotion_line"]
            print(f"[Director] Resumed from checkpoint. {len(completed_indices)} lines already done.")

    chunks = chunk_segments(segments, chunk_size=20)
    global_idx = 0

    for chunk_num, chunk in enumerate(chunks):
        chunk_indices = list(range(global_idx, global_idx + len(chunk)))
        if all(i in completed_indices for i in chunk_indices):
            print(f"[Director] Chunk {chunk_num+1}/{len(chunks)} already complete, skipping.")
            global_idx += len(chunk)
            continue

        print(f"[Director] Processing chunk {chunk_num+1}/{len(chunks)} ({len(chunk)} lines)...")
        prompt = build_user_prompt(chunk, show_name, episode_context, cast_map=cast_map)

        for attempt in range(retries):
            try:
                raw = _ollama_generate(prompt, ollama_model, retries=1).strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                raw = raw.strip()

                parsed = json.loads(raw)
                emotion_lines = parsed.get("lines", [])

                if len(emotion_lines) != len(chunk):
                    raise ValueError(f"Line count mismatch: got {len(emotion_lines)}, expected {len(chunk)}")

                # ── Output hallucination guard ─────────────────────────────
                _out_counts = Counter(l.strip() for l in emotion_lines)
                _repeated = {l for l, c in _out_counts.items() if c > 3}
                if _repeated:
                    print(f"[Director] WARNING: Ollama repeated {len(_repeated)} emotion line(s) >3x — reverting to neutral fallback", flush=True)
                    emotion_lines = [
                        f"[neutral] {chunk[i].get('translated_text', chunk[i].get('text', ''))}"
                        if line.strip() in _repeated else line
                        for i, line in enumerate(emotion_lines)
                    ]

                for i, line in enumerate(emotion_lines):
                    segments[global_idx + i]["emotion_line"] = line

                if checkpoint_file:
                    checkpoint_data = [
                        {**seg, "original_index": idx}
                        for idx, seg in enumerate(segments)
                        if "emotion_line" in seg
                    ]
                    with open(checkpoint_file, "w", encoding="utf-8") as f:
                        json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
                break

            except Exception as e:
                print(f"[Director] Attempt {attempt+1} failed: {e}")
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    print(f"[Director] Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"[Director] Chunk {chunk_num+1} failed. Using raw translation as fallback.")
                    for i, seg in enumerate(chunk):
                        segments[global_idx + i]["emotion_line"] = (
                            f"[neutral] {seg.get('translated_text', seg.get('text', ''))}"
                        )

        global_idx += len(chunk)
        time.sleep(0.5)

    return segments
