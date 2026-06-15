"""
apply_scene_cast.py
Assigns verified character names to every line in state_director.json
using the human-verified scene context timestamps.
For single-speaker scenes: assigns directly.
For multi-speaker scenes: uses the old hallucinated name as a hint via NAME_MAP.
"""
import json, re, shutil
from pathlib import Path
from datetime import datetime

STATE   = Path(r"jobs/smoking_supermarket_s01e01/state_director.json")
CONTEXT = Path(r"characters/shows/smoking_behind_the_supermarket_with_you/s01e01_scene_context.json")

# ---------- name map: hallucinated → correct (used for multi-speaker scenes) ----------
NAME_MAP = {
    "sasaki":           "sasaki_male_lead",
    "sasakisan":        "sasaki_male_lead",
    "sakaki":           "sasaki_male_lead",
    "smoker_guy":       "sasaki_male_lead",
    "old_man":          "sasaki_male_lead",   # LLM called Sasaki "old man"
    "tayama":           "tayama",
    "yamada":           "yamada",
    "yamadasan":        "yamada",
    "taro_yamada":      "yamada",
    "taro":             "yamada",
    "store_clerk":      "yamada",             # refined by scene below
    "store_employee":   "yamada",
    "natsumi":          "older_lady_clerk_female_supporting",
    "customer":         "female_passerby_generic",
    "hiroshi_nakamura": "suzuki_male_supporting",
    "kenji_sato":       "chief_male_supporting",
    "tanaka":           "chief_male_supporting",
    "akira":            "office_worker_male_background",
    "office_worker":    "office_worker_male_background",
}

# Fallback when name_map has no match
UNKNOWN_FALLBACK = "sasaki_male_lead"

def ts_to_sec(ts: str) -> float:
    """'7:42' → 462.0"""
    parts = ts.strip().split(":")
    return int(parts[0]) * 60 + float(parts[1])

def parse_range(ts_range: str):
    """'7:00-7:42' → (420.0, 462.0)"""
    parts = ts_range.split("-")
    return ts_to_sec(parts[0]), ts_to_sec(parts[1])

def map_name(old: str) -> str:
    return NAME_MAP.get(old.lower().strip(), UNKNOWN_FALLBACK)

# ---------- load ----------
state   = json.loads(STATE.read_text(encoding="utf-8"))
context = json.loads(CONTEXT.read_text(encoding="utf-8"))
lines   = state["lines"]

scenes = []
for s in context["scenes"]:
    try:
        start, end = parse_range(s["timestamp"])
        scenes.append({"start": start, "end": end, "speakers": s["speakers"], "note": s.get("note","")})
    except Exception as e:
        print(f"  [warn] Could not parse timestamp '{s['timestamp']}': {e}")

scenes.sort(key=lambda s: s["start"])

# ---------- outro / singer special case ----------
SINGER_SCENES = {s["start"] for s in scenes if "singer" in " ".join(s["speakers"])}

def find_scene(sec: float):
    """Return the scene dict whose range contains sec, or None."""
    for s in scenes:
        if s["start"] <= sec < s["end"] + 1.0:   # +1s tolerance for edge lines
            return s
    return None

def assign(line: dict) -> str:
    sec      = line.get("start", 0)
    old_char = line.get("character", "")
    scene    = find_scene(sec)

    if scene is None:
        # Outside all defined scenes — keep mapped name
        return map_name(old_char)

    speakers = scene["speakers"]

    # Single speaker → always assign directly
    if len(speakers) == 1:
        return speakers[0]

    # Multi-speaker → if current char is already a valid speaker, keep it
    if old_char in speakers:
        return old_char

    # Try name map
    mapped = map_name(old_char)
    if mapped in speakers:
        return mapped

    # Fallback: first listed speaker (most prominent in scene notes)
    return speakers[0]

# ---------- apply ----------
changed = 0
char_before = {}
char_after  = {}

for line in lines:
    old = line.get("character", "")
    new = assign(line)
    char_before[old] = char_before.get(old, 0) + 1
    char_after[new]  = char_after.get(new, 0)  + 1
    if old != new:
        line["character"] = new
        changed += 1

# ---------- backup + write ----------
backup = STATE.with_suffix(".json.bak")
shutil.copy2(STATE, backup)
print(f"Backed up -> {backup}")

tmp = STATE.with_suffix(".json.tmp")
tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
tmp.replace(STATE)

# ---------- report ----------
print(f"\nChanged {changed}/{len(lines)} lines\n")
print("BEFORE:")
for k,v in sorted(char_before.items(), key=lambda x:-x[1]):
    print(f"  {k:35s} {v}")
print("\nAFTER:")
for k,v in sorted(char_after.items(), key=lambda x:-x[1]):
    print(f"  {k:35s} {v}")
