"""
run_diarize_ollama.py — One-shot diarize using Ollama llama3.1:8b
Reads state_whisper.json, assigns characters via Ollama, writes state_director.json
Run: python run_diarize_ollama.py --ep smoking_supermarket_s01e01
"""
import argparse, json, os, sys, time, urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.1:8b"
CHUNK = 80  # lines per Ollama call to stay within context


def ollama(prompt: str) -> str:
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "format": "json"}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["response"]


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def diarize_chunk(chunk: list, chunk_idx: int, total_chunks: int) -> list:
    n = len(chunk)
    prompt = f"""You are a script supervisor for a Japanese anime dubbing project.
Assign character names and emotions to each line of dialogue.
This is chunk {chunk_idx+1} of {total_chunks}.

Lines (i=index, s=start_sec, e=end_sec, t=text):
{json.dumps(chunk)}

Return ONLY a JSON array of exactly {n} objects, one per input line IN ORDER:
[{{"i":<index>,"c":"<CharacterName>","em":"<emotion>","tp":"<speech|singing>"}}]

Emotions: neutral cheerful angry sad whisper exhausted excited fearful
Use consistent names — same person always gets the same name.
Output {n} objects. Count them. Raw JSON only, no markdown."""

    print(f"  Chunk {chunk_idx+1}/{total_chunks}: {n} lines -> Ollama ...", flush=True)
    t0 = time.time()
    raw = ollama(prompt)
    elapsed = round(time.time() - t0, 1)
    print(f"  Done in {elapsed}s", flush=True)

    text = strip_fences(raw)
    parsed = json.loads(text)

    # Unwrap various response shapes
    if isinstance(parsed, list):
        result = parsed
    elif isinstance(parsed, dict):
        # Try known keys first, then any key whose value is a list
        for key in ("lines", "data", "results", "assignments"):
            if key in parsed and isinstance(parsed[key], list):
                result = parsed[key]
                break
        else:
            lists = [v for v in parsed.values() if isinstance(v, list)]
            if lists:
                result = max(lists, key=len)  # pick the longest list
            else:
                raise ValueError(f"Chunk {chunk_idx+1}: response has no list — got: {str(parsed)[:200]}")
    else:
        raise ValueError(f"Chunk {chunk_idx+1}: unexpected response type {type(parsed)} — {str(parsed)[:200]}")

    if len(result) != n:
        raise ValueError(f"Chunk {chunk_idx+1}: got {len(result)} items, expected {n}. Sample: {str(result[:2])}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True)
    args = ap.parse_args()
    ep = args.ep

    job_dir = Path("jobs") / ep
    whisper_path = job_dir / "state_whisper.json"
    director_path = job_dir / "state_director.json"
    status_path = job_dir / "status_diarize.json"

    if not whisper_path.exists():
        sys.exit(f"ERROR: {whisper_path} not found")

    segments = json.loads(whisper_path.read_text(encoding="utf-8"))
    N = len(segments)
    print(f"Diarizing {N} segments via Ollama {MODEL} ...", flush=True)

    # Compact input
    compact = [{"i": s.get("id", i), "s": round(s["start"], 1), "e": round(s["end"], 1), "t": s["text"]}
               for i, s in enumerate(segments)]

    # Chunk processing
    all_results = []
    chunks = [compact[i:i+CHUNK] for i in range(0, N, CHUNK)]
    for idx, chunk in enumerate(chunks):
        retries = 3
        for attempt in range(retries):
            try:
                results = diarize_chunk(chunk, idx, len(chunks))
                all_results.extend(results)
                break
            except Exception as e:
                print(f"  Attempt {attempt+1} failed: {e}", flush=True)
                if attempt == retries - 1:
                    raise
                time.sleep(5)

    if len(all_results) != N:
        sys.exit(f"ERROR: got {len(all_results)} total results, expected {N}")

    # Detect songs (lines marked as singing)
    songs = []
    seen_segments = set()
    for r in all_results:
        if r.get("tp") == "singing":
            seg = "intro" if r["i"] < N // 2 else "outro"
            if seg not in seen_segments:
                seen_segments.add(seg)
                seg_start = segments[r["i"]]["start"]
                seg_end = segments[r["i"]]["end"]
                songs.append({"segment": seg, "artist": "", "start": seg_start, "end": seg_end,
                              "song_source": "generate", "lyrics_source": "", "lyrics_english": "",
                              "path_mode": "A", "dubbed_wav": "", "vault_wav": "", "status": "pending"})

    # Load existing director state
    existing = json.loads(director_path.read_text(encoding="utf-8")) if director_path.exists() else {}
    existing_by_idx = {l["line_index"]: l for l in existing.get("lines", [])}

    pending = {"standard": "pending", "aave": "pending"}
    expanded = []
    for r in all_results:
        i = r.get("i", all_results.index(r))
        seg = segments[i] if i < len(segments) else {}
        prev = existing_by_idx.get(i, {})
        expanded.append({
            "line_index": i,
            "type": r.get("tp", "speech"),
            "character": r.get("c", f"Speaker_{i}"),
            "start": seg.get("start", prev.get("start", 0)),
            "end":   seg.get("end",   prev.get("end",   0)),
            "source_text":   seg.get("text", prev.get("source_text", "")),
            "text_standard": prev.get("text_standard", ""),
            "text_aave":     prev.get("text_aave", ""),
            "detected_emotion": r.get("em", "neutral"),
            "voice_id":  prev.get("voice_id"),
            "clip_path": prev.get("clip_path"),
            "audio_synthesis_status": prev.get("audio_synthesis_status", dict(pending)),
            "audio_fit_status":       prev.get("audio_fit_status",       dict(pending)),
            "raw_wav":  prev.get("raw_wav",  {"standard": "", "aave": ""}),
            "fit_wav":  prev.get("fit_wav",  {"standard": "", "aave": ""}),
            "synthesis_quality": prev.get("synthesis_quality", dict(pending)),
            "mos_score": prev.get("mos_score", {"standard": None, "aave": None}),
            "error_msg": prev.get("error_msg"),
        })

    # Unique characters
    characters = {}
    for line in expanded:
        c = line["character"]
        if c not in characters:
            characters[c] = existing.get("characters", {}).get(c, {
                "voice_id": None, "bank_complete": False, "line_count": 0
            })
        characters[c]["line_count"] = characters[c].get("line_count", 0) + 1

    new_state = {**existing,
                 "ep_folder": ep, "cast_locked": False,
                 "characters": characters, "songs": songs or existing.get("songs", []),
                 "lines": expanded}

    tmp = str(director_path) + ".tmp"
    Path(tmp).write_text(json.dumps(new_state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, director_path)

    status = {"stage": "diarize", "status": "done", "progress": 100,
              "line_count": N, "character_count": len(characters),
              "characters": list(characters.keys()), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    Path(tmp).write_text(json.dumps(status, indent=2), encoding="utf-8")
    os.replace(tmp, status_path)

    print(f"\nDone. {N} lines, {len(characters)} characters: {list(characters.keys())}", flush=True)
    print(f"state_director.json → {director_path}", flush=True)


if __name__ == "__main__":
    main()
