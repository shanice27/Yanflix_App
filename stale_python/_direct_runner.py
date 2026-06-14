"""
_direct_runner.py — adds Ollama emotion/acting tags to translated segments.
Reads a segments JSON, runs the Director, writes emotion_line back.
Supports --style standard|dialect to direct the matching translation field.
"""
import argparse, json, sys
from pathlib import Path

_YANFLIX_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_YANFLIX_DIR))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--segments",  required=True)
    p.add_argument("--output",    default="")
    p.add_argument("--show_name", default="")
    p.add_argument("--model",     default="llama3.1:8b")
    p.add_argument("--status",    required=True)
    p.add_argument("--cast",      default="")
    p.add_argument("--style",     default="standard", choices=["standard", "dialect"])
    args = p.parse_args()

    style       = args.style
    text_field  = "translated_text" if style == "standard" else "translated_text_dialect"
    out_field   = "emotion_line"    if style == "standard" else "emotion_line_dialect"
    label       = "Standard" if style == "standard" else "AAVE Dialect"

    # Load cast mapping so director knows who's speaking
    cast_map = {}
    if args.cast:
        try:
            cast_data   = json.loads(Path(args.cast).read_text(encoding="utf-8"))
            assignments = cast_data.get("assignments", {})
            characters  = cast_data.get("characters", {})
            for spk, char_id in assignments.items():
                char = characters.get(char_id, {})
                cast_map[spk] = char.get("name") or char_id.replace("_", " ").title()
            print(f"[Direct/{label}] Cast loaded: {cast_map}", flush=True)
        except Exception as e:
            print(f"[Direct/{label}] Could not load cast: {e}", flush=True)

    status_file = Path(args.status)
    segs_file   = Path(args.segments)
    out_file    = Path(args.output) if args.output else segs_file

    def write_status(data):
        status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    write_status({"status": "running", "style": style})

    data     = json.loads(segs_file.read_text(encoding="utf-8"))
    segments = data if isinstance(data, list) else data.get("segments", [])

    # Stamp character names so director can label lines correctly
    if cast_map:
        for seg in segments:
            spk = seg.get("speaker", "")
            if spk in cast_map:
                seg["character_name"] = cast_map[spk]

    # Point director at the right translation field for this style
    # We temporarily set translated_text = the style's field so director.py
    # build_user_prompt reads the right text without needing a style param itself.
    if style == "dialect":
        for seg in segments:
            if "translated_text_dialect" in seg:
                seg["_translated_text_backup"] = seg.get("translated_text", "")
                seg["translated_text"] = seg["translated_text_dialect"]

    from director import apply_emotion_tags
    checkpoint_dir = segs_file.parent
    # Use a style-specific checkpoint file so the two passes don't collide
    import director as _director_mod
    _orig_checkpoint_name = "director_checkpoint.json"
    _style_checkpoint     = f"director_checkpoint_{style}.json"

    directed = apply_emotion_tags(
        segments,
        show_name=args.show_name,
        checkpoint_path=checkpoint_dir,
        ollama_model=args.model,
        cast_map=cast_map,
        checkpoint_filename=_style_checkpoint,
    )

    # Restore translated_text and move emotion_line → out_field
    for seg in directed:
        el = seg.pop("emotion_line", None)
        if el is not None:
            seg[out_field] = el
        if style == "dialect" and "_translated_text_backup" in seg:
            seg["translated_text"] = seg.pop("_translated_text_backup")

    if isinstance(data, list):
        out_file.write_text(json.dumps(directed, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        data["segments"] = directed
        out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    write_status({"status": "done", "total": len(directed), "style": style})
    print(f"[Direct/{label}] Done — {len(directed)} segments directed → field: {out_field}", flush=True)


if __name__ == "__main__":
    main()
