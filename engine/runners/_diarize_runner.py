"""
_diarize_runner.py — called by serve_ui.py via conda run -n sonitr
Runs pyannote speaker diarization on vocals.wav, auto-merges speakers
that are acoustically similar (same character, different emotions), then
extracts one sample clip per speaker and writes .diarize_status JSON.
"""
import argparse, json, subprocess, sys
from pathlib import Path
from collections import defaultdict

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--vocals",          required=True)
    p.add_argument("--out_dir",         required=True)
    p.add_argument("--hf_token",        required=True)
    p.add_argument("--status",          required=True)
    p.add_argument("--num_speakers",    type=int,   default=0)
    p.add_argument("--min_speakers",    type=int,   default=0)
    p.add_argument("--max_speakers",    type=int,   default=0)
    p.add_argument("--batch_size",      type=int,   default=64)
    p.add_argument("--merge_threshold", type=float, default=0.82,
                   help="Cosine similarity threshold for merging same-person speakers (0=off)")
    args = p.parse_args()

    vocals_path = Path(args.vocals)
    out_dir     = Path(args.out_dir)
    status_file = Path(args.status)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Downsample to 16kHz mono — pyannote needs no more than this, and it's ~60% faster
    resampled = out_dir / "_vocals_16k.wav"
    if not resampled.exists():
        print("[diarize] Resampling to 16kHz mono for faster diarization…", flush=True)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(vocals_path),
            "-ar", "16000", "-ac", "1", str(resampled),
        ], capture_output=True, check=True)
    diarize_input = resampled

    def write_status(data):
        status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    write_status({"status": "running"})

    try:
        from pyannote.audio import Pipeline, Inference
        import torch
        import numpy as np

        # PyTorch 2.6 changed weights_only default to True; pyannote models need False
        original_torch_load = torch.load
        def patched_torch_load(*a, **kw):
            kw["weights_only"] = False
            return original_torch_load(*a, **kw)
        torch.load = patched_torch_load

        print(f"[diarize] Loading pyannote pipeline…", flush=True)
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=args.hf_token,
        )
        device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        pipeline = pipeline.to(device)
        print(f"[diarize] Using {'CUDA' if device.type == 'cuda' else 'CPU'}", flush=True)

        if device.type == "cuda":
            try:
                pipeline.segmentation.batch_size = args.batch_size
                print(f"[diarize] Segmentation batch_size={args.batch_size}", flush=True)
            except Exception:
                pass

        print(f"[diarize] Running diarization on 16kHz resample…", flush=True)
        spk_kwargs = {}
        if args.num_speakers > 0:
            spk_kwargs["num_speakers"] = args.num_speakers
        else:
            if args.min_speakers > 0: spk_kwargs["min_speakers"] = args.min_speakers
            if args.max_speakers > 0: spk_kwargs["max_speakers"] = args.max_speakers
        if spk_kwargs:
            print(f"[diarize] Speaker hint: {spk_kwargs}", flush=True)
        diarization = pipeline(str(diarize_input), **spk_kwargs)

        segments_by_speaker = defaultdict(list)
        all_segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            seg = {"speaker": speaker, "start": round(turn.start, 3), "end": round(turn.end, 3)}
            all_segments.append(seg)
            segments_by_speaker[speaker].append(seg)

        speakers = sorted(segments_by_speaker.keys())
        print(f"[diarize] Detected {len(speakers)} raw speaker(s): {speakers}", flush=True)

        # ── Auto-merge acoustically similar speakers ──────────────────────────
        # Uses the embedding model already inside the pipeline — no extra download.
        # Speakers above merge_threshold cosine similarity are assumed to be the
        # same character with different emotional delivery (pyannote over-segments).
        if args.merge_threshold > 0 and len(speakers) > 1:
            try:
                import torchaudio
                inference = Inference(pipeline.embedding, window="whole")
                print(f"[diarize] Computing speaker embeddings (merge_threshold={args.merge_threshold})…", flush=True)

                embeddings = {}
                for spk in speakers:
                    segs = sorted(segments_by_speaker[spk], key=lambda s: s["end"] - s["start"], reverse=True)
                    best = segs[0]
                    waveform, sr = torchaudio.load(str(diarize_input))
                    start_frame = int(best["start"] * sr)
                    end_frame   = int(min(best["start"] + 15.0, best["end"]) * sr)
                    crop = waveform[:, start_frame:end_frame]
                    emb = inference({"waveform": crop, "sample_rate": sr})
                    embeddings[spk] = np.array(emb).flatten()

                def cosine_sim(a, b):
                    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

                # Union-Find
                parent = {spk: spk for spk in speakers}
                def find(x):
                    while parent[x] != x:
                        parent[x] = parent[parent[x]]
                        x = parent[x]
                    return x
                def union(x, y):
                    parent[find(x)] = find(y)

                pairs = sorted(
                    [(cosine_sim(embeddings[a], embeddings[b]), a, b)
                     for i, a in enumerate(speakers) for b in speakers[i+1:]],
                    reverse=True
                )
                merged_any = False
                for sim, a, b in pairs:
                    if sim >= args.merge_threshold:
                        print(f"[diarize] Merging {a} + {b} (sim={sim:.3f})", flush=True)
                        union(a, b)
                        merged_any = True

                if merged_any:
                    unique_roots = sorted(set(find(s) for s in speakers))
                    root_to_new  = {r: f"SPEAKER_{i:02d}" for i, r in enumerate(unique_roots)}
                    final_map    = {spk: root_to_new[find(spk)] for spk in speakers}
                    print(f"[diarize] After merge: {len(speakers)} → {len(unique_roots)} speaker(s)", flush=True)
                    for spk in speakers:
                        if final_map[spk] != spk:
                            print(f"[diarize]   {spk} → {final_map[spk]}", flush=True)

                    for seg in all_segments:
                        seg["speaker"] = final_map[seg["speaker"]]

                    new_by_spk = defaultdict(list)
                    for seg in all_segments:
                        new_by_spk[seg["speaker"]].append(seg)
                    segments_by_speaker = new_by_spk
                    speakers = sorted(segments_by_speaker.keys())
                else:
                    print(f"[diarize] No speakers merged — all pairs below threshold", flush=True)

            except Exception as emb_err:
                print(f"[diarize] Embedding merge skipped: {emb_err}", flush=True)

        # ── Extract best sample per (merged) speaker ─────────────────────────
        samples = {}
        for spk in speakers:
            spk_dir = out_dir / spk
            spk_dir.mkdir(exist_ok=True)
            segs = sorted(segments_by_speaker[spk], key=lambda s: s["end"] - s["start"], reverse=True)
            best     = segs[0]
            duration = min(best["end"] - best["start"], 15.0)
            sample   = spk_dir / "sample.wav"
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(best["start"]),
                "-t",  str(duration),
                "-i",  str(vocals_path),
                str(sample),
            ], capture_output=True)
            try:
                root = out_dir.parent.parent.parent.parent
                rel  = str(sample.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(sample).replace("\\", "/")
            samples[spk] = {
                "sample": rel,
                "segment_count": len(segments_by_speaker[spk]),
                "sample_start": round(best["start"], 2),
                "sample_end":   round(min(best["start"] + duration, best["end"]), 2),
            }
            print(f"[diarize] {spk}: {len(segs)} segments, sample → {sample.name}", flush=True)

        (out_dir / "diarization.json").write_text(
            json.dumps({"speakers": speakers, "segments": all_segments, "samples": samples}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        write_status({"status": "done", "speakers": speakers, "samples": samples})
        print(f"[diarize] Done — {len(speakers)} speaker(s)", flush=True)

    except Exception as exc:
        import traceback
        traceback.print_exc()
        write_status({"status": "error", "error": str(exc)})
        sys.exit(1)

if __name__ == "__main__":
    main()
