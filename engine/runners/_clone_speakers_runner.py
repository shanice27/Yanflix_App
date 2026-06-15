"""
_clone_speakers_runner.py — called by serve_ui.py via conda run -n sonitr
Clones each speaker's voice using IndexTTS-2 via Gradio API.
Hard rule: American English — reference audio shapes timbre only, not accent.
"""
import argparse, json, shutil, sys
from pathlib import Path

AVATAR_TEXT = (
    "Hey, it's good to finally meet you. "
    "I've heard a lot about this place, and honestly, it's exactly what I expected. "
    "Let's just take it one step at a time and see where things go from here."
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--speakers_json",  required=True)
    p.add_argument("--show_slug",      required=True)
    p.add_argument("--characters_dir", required=True)
    p.add_argument("--status",         required=True)
    args = p.parse_args()

    status_file    = Path(args.status)
    characters_dir = Path(args.characters_dir)
    # speakers_json is a file path written by serve_ui.py
    speakers       = json.loads(Path(args.speakers_json).read_text(encoding="utf-8"))

    def write_status(data):
        status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    write_status({"status": "running", "done": [], "total": len(speakers)})

    try:
        from gradio_client import Client, handle_file
    except ImportError:
        write_status({"status": "error", "error": "gradio_client not installed — run: pip install gradio_client"})
        sys.exit(1)

    client = Client("IndexTeam/IndexTTS-2-Demo")
    done    = []
    results = {}

    for spk_id, info in speakers.items():
        sample_path = info.get("sample")
        if not sample_path:
            print(f"[clone] {spk_id}: no sample, skipping", flush=True)
            continue

        sample_abs = Path(sample_path)
        if not sample_abs.is_absolute():
            sample_abs = Path(args.characters_dir).parent / sample_path
        if not sample_abs.exists():
            print(f"[clone] {spk_id}: sample not found at {sample_abs}", flush=True)
            results[spk_id] = {"error": "sample file not found"}
            write_status({"status": "running", "done": done, "total": len(speakers), "results": results})
            continue

        out_dir = characters_dir / "shows" / args.show_slug / spk_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_wav = out_dir / "avatar_monologue.wav"

        print(f"[clone] Cloning {spk_id} via IndexTTS-2…", flush=True)
        try:
            result = client.predict(
                emo_control_method="Same as the voice reference",
                prompt=handle_file(str(sample_abs)),
                text=AVATAR_TEXT,
                emo_ref_path=handle_file(str(sample_abs)),  # required field, unused in this mode
                emo_weight=0.8,
                vec1=0, vec2=0, vec3=0, vec4=0,
                vec5=0, vec6=0, vec7=0, vec8=0,
                emo_text="",
                emo_random=False,
                max_text_tokens_per_segment=120,
                param_16=True,   # do_sample
                param_17=0.8,    # top_p
                param_18=30,     # top_k
                param_19=0.8,    # temperature
                param_20=0,      # length_penalty
                param_21=3,      # num_beams
                param_22=10,     # repetition_penalty
                param_23=1500,   # max_mel_tokens
                api_name="/gen_single",
            )
            # result is a filepath string to the generated audio
            shutil.copy2(result, out_wav)
            done.append(spk_id)
            results[spk_id] = {"avatar": str(out_wav).replace("\\", "/")}
            # Write meta so the vault can regen this speaker later
            meta = {
                "speaker_id": spk_id,
                "show_slug": args.show_slug,
                "sample_path": str(sample_abs).replace("\\", "/"),
                "cloned_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            }
            (out_dir / "meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            print(f"[clone] {spk_id} done → {out_wav.name}", flush=True)

        except Exception as e:
            print(f"[clone] {spk_id} FAILED: {e}", flush=True)
            results[spk_id] = {"error": str(e)}

        write_status({"status": "running", "done": done, "total": len(speakers), "results": results})

    write_status({"status": "done", "done": done, "total": len(speakers), "results": results})
    print(f"[clone] All done — {len(done)}/{len(speakers)} cloned", flush=True)


if __name__ == "__main__":
    main()
