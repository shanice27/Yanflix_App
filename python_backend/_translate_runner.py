"""
_translate_runner.py — translates raw segment text to English using Ollama.
Supports two styles:
  standard — clean natural American English
  dialect  — authentic AAVE (African American Vernacular English)
Reads state_director.json, adds translated_text (standard) or
translated_text_dialect (dialect) to each segment.
"""
import argparse, json, re, sys, time
from collections import Counter
from pathlib import Path

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(__file__))
from llm_client import chat as llm_chat

CHUNK_SIZE = 15

SYSTEM_STANDARD = """You are a professional dubbing translator working on animated and live-action content.
Translate each numbered line into natural spoken American English suitable for voice acting.
The source language will be specified in the request — translate accurately regardless of source language.
Keep character names and proper nouns unchanged.
Match the emotional register of the original — casual speech stays casual, formal stays formal.
Preserve honorifics and culturally specific terms only when they have no natural English equivalent.
Never use asterisks, dashes, or euphemisms to obscure any words — write all language fully and directly.
Output ONLY valid JSON. No markdown, no explanation.
Format: {"lines": ["English translation 1", "English translation 2", ...]}"""

SYSTEM_DIALECT = """You are a dubbing translator specializing in authentic African American Vernacular English (AAVE) for animated and live-action content.
Translate each numbered line into natural AAVE — the everyday spoken dialect used in urban Black American communities.
Use authentic AAVE grammar, vocabulary, slang, and rhythm. Do not code-switch to Standard American English.
The source language will be specified in the request — translate the meaning and emotional register accurately.
Keep character names and proper nouns unchanged.
Never use asterisks, dashes, or euphemisms to obscure any words — write all language fully and directly.
Match the emotional register: if someone is excited, the AAVE should be excited; if they are defeated, let it show.
Output ONLY valid JSON. No markdown, no explanation.
Format: {"lines": ["AAVE translation 1", "AAVE translation 2", ...]}"""


def ollama_translate(lines: list, speakers: list, model: str, system: str, cast_context: str = "", source_lang: str = "") -> list:
    sys_prompt = system
    if cast_context:
        sys_prompt += f"\n\n{cast_context}\nUse character names to guide tone — each character has a distinct voice."
    lang_note = f"Source language: {source_lang}. " if source_lang else ""
    numbered  = "\n".join(f"{i+1}. [{speakers[i]}] {l}" if speakers[i] else f"{i+1}. {l}" for i, l in enumerate(lines))

    for attempt in range(4):
        try:
            raw = llm_chat(
                messages=[{"role": "user", "content": f"{lang_note}Translate these lines to English:\n{numbered}"}],
                system=sys_prompt,
                json_mode=True,
            ).strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            parsed = json.loads(raw.strip())
            result = parsed.get("lines", [])
            if len(result) == len(lines):
                result = [re.sub(r'^\[SPEAKER_\d+\]\s*', '', l).strip() for l in result]
                return result
            print(f"[Translate] Warning: got {len(result)} lines for {len(lines)} — retrying", flush=True)
        except Exception as e:
            wait = 2 ** attempt
            print(f"[Translate] Attempt {attempt+1} failed: {e}. Retrying in {wait}s…", flush=True)
            time.sleep(wait)
    return lines  # fallback: return originals


def build_cast_context(cast_file: Path) -> str:
    try:
        cast  = json.loads(cast_file.read_text(encoding="utf-8"))
        chars = cast.get("characters", {})
        assignments = cast.get("assignments", {})
        lines = []
        for spk, char_id in sorted(assignments.items()):
            char  = chars.get(char_id, {})
            name  = char.get("name") or char_id.replace("_", " ").title()
            notes = char.get("notes", "")
            lines.append(f"  {spk} = {name}" + (f" ({notes})" if notes else ""))
        if lines:
            show   = cast.get("show_name", "")
            header = f"Show: {show}\nCharacter roster:\n" if show else "Character roster:\n"
            return header + "\n".join(lines)
    except Exception:
        pass
    return ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--segments",    required=True)
    p.add_argument("--output",      default="")
    p.add_argument("--model",       default="llama3.1:8b")
    p.add_argument("--status",      required=True)
    p.add_argument("--cast",        default="")
    p.add_argument("--source_lang", default="")
    p.add_argument("--style",       default="standard", choices=["standard", "dialect"],
                   help="Translation style: standard (clean American English) or dialect (AAVE)")
    args = p.parse_args()

    style       = args.style
    system      = SYSTEM_STANDARD if style == "standard" else SYSTEM_DIALECT
    out_field   = "translated_text" if style == "standard" else "translated_text_dialect"
    label       = "Standard" if style == "standard" else "AAVE Dialect"

    status_file = Path(args.status)
    segs_file   = Path(args.segments)
    out_file    = Path(args.output) if args.output else segs_file

    cast_context = build_cast_context(Path(args.cast)) if args.cast else ""
    if cast_context:
        print(f"[Translate/{label}] Cast context loaded", flush=True)

    def write_status(data):
        status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    write_status({"status": "running", "done": 0, "total": 0, "style": style})

    data     = json.loads(segs_file.read_text(encoding="utf-8"))
    segments = data if isinstance(data, list) else data.get("segments", [])

    # ── Hallucination guard — strip Whisper repeats before Ollama sees them ──
    _src_counts      = Counter(s["text"].strip() for s in segments)
    _src_hallucinated = {t for t, c in _src_counts.items() if c > 3}
    if _src_hallucinated:
        _before  = len(segments)
        segments = [s for s in segments if s["text"].strip() not in _src_hallucinated]
        for _i, _s in enumerate(segments):
            _s["id"] = _i
        print(f"[Translate/{label}] Stripped {_before - len(segments)} hallucinated source segments", flush=True)

    # Skip lines already translated (checkpoint resume)
    out_field_check = "translated_text" if args.style == "standard" else "translated_text_dialect"
    dialogue = [(i, s) for i, s in enumerate(segments) if not s.get("isSong") and s.get("text") and not s.get(out_field_check)]
    already_done = sum(1 for s in segments if s.get(out_field_check))
    if already_done:
        print(f"[Translate/{label}] Resuming — {already_done} lines already translated, {len(dialogue)} remaining", flush=True)
    write_status({"status": "running", "done": already_done, "total": len(dialogue) + already_done, "style": style})
    print(f"[Translate/{label}] {len(dialogue)} dialogue lines remaining to translate ({already_done} already done)", flush=True)

    done = 0
    for chunk_start in range(0, len(dialogue), CHUNK_SIZE):
        chunk          = dialogue[chunk_start:chunk_start + CHUNK_SIZE]
        speaker_labels = [segments[i].get("speaker", "") for i, _ in chunk]
        texts          = [segments[i]["text"] for i, _ in chunk]
        translations   = ollama_translate(texts, speaker_labels, args.model, system, cast_context, args.source_lang)

        # ── Output hallucination guard ────────────────────────────────────
        _out_counts = Counter(t.strip() for t in translations)
        _repeated   = {t for t, c in _out_counts.items() if c > 3}
        if _repeated:
            print(f"[Translate/{label}] WARNING: Ollama repeated {len(_repeated)} phrase(s) >3x — reverting to source", flush=True)
            translations = [src if tr.strip() in _repeated else tr for src, tr in zip(texts, translations)]

        for (idx, _), tr in zip(chunk, translations):
            segments[idx][out_field] = tr
        done += len(chunk)

        # Write to disk after every chunk so progress survives a crash or kill
        if isinstance(data, list):
            out_file.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            data["segments"] = segments
            out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        write_status({"status": "running", "done": done + already_done, "total": len(dialogue) + already_done, "style": style})
        print(f"[Translate/{label}] {done + already_done}/{len(dialogue) + already_done} done", flush=True)

    if isinstance(data, list):
        out_file.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        data["segments"] = segments
        out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    write_status({"status": "done", "done": done, "total": len(dialogue), "style": style})
    print(f"[Translate/{label}] Done — {done} lines translated → field: {out_field}", flush=True)


if __name__ == "__main__":
    main()
