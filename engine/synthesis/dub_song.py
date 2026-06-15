"""
dub_song.py — Song dubbing pipeline (GPU)
==========================================
Path A (default, $0):
  Gemini singable translation → IndexTTS2 with artist seed refs → Rubberband fit → mix over instrumental

Path B (human guide vocal):
  Guide vocal WAV → RVC timbre conversion → mix over instrumental

Args:
  --job_dir         jobs/{ep_folder}/
  --segment         intro | outro | song_N
  --path_mode       A | B
  --show            show slug
  --characters_root characters/
  --is_series       true | false  (if true, copy result to song vault after Path A)

Writes:
  jobs/{ep}/songs/{segment}_dubbed_{track}.wav
  jobs/{ep}/status_song_{segment}.json
  If is_series=true: characters/shows/{show}/songs/{segment}_standard.wav
  Removes gpu.lock in finally
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

GPU_LOCK = Path("jobs/gpu.lock")


def write_status(job_dir: Path, segment: str, payload: dict):
    p = job_dir / f"status_song_{segment}.json"
    cur: dict = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(payload)
    cur["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def save_state(state_path: Path, state: dict):
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)


def log(job_dir: Path, segment: str, msg: str):
    print(msg, flush=True)
    p = job_dir / f"status_song_{segment}.json"
    logs: list = []
    if p.exists():
        try:
            logs = json.loads(p.read_text(encoding="utf-8")).get("logs", [])
        except Exception:
            logs = []
    logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    write_status(job_dir, segment, {"logs": logs[-100:]})


def gemini_lyric_translation(lines: list[dict], source_lang: str, api_key: str) -> list[dict]:
    """Single Gemini call for singable syllable-matched translation."""
    import urllib.request, urllib.error
    MODELS = ['gemini-2.0-flash', 'gemini-1.5-flash']

    prompt_path = Path("prompts/03_song_translation.md")
    system = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    prompt = f"""{system}

Source language: {source_lang}
Lines:
{json.dumps(lines)}

Return ONLY a JSON array with fields:
line_index, source_text, lyrics_english, syllable_count_source, syllable_count_english
"""
    for i, model in enumerate(MODELS):
        if i > 0:
            time.sleep(35)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
        except Exception as e:
            print(f"[dub_song] Gemini {model} failed: {e}", flush=True)
            continue
    raise RuntimeError("All Gemini models failed for lyric translation")


def synthesize_lyrics_indextts(lyric_lines: list[dict], ref_wav: Path,
                                out_dir: Path, model) -> list[Path]:
    """Synthesize each lyric line via IndexTTS2 with artist ref."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []
    for i, line in enumerate(lyric_lines):
        text = line.get("lyrics_english", "")
        if not text.strip():
            continue
        out_path = out_dir / f"lyric_{i:03d}.wav"
        if out_path.exists():
            out_paths.append(out_path)
            continue
        model.infer(audio_prompt=str(ref_wav), text=text, output_path=str(out_path))
        out_paths.append(out_path)
    return out_paths


def rubberband_fit(in_wav: Path, out_wav: Path, target_dur: float) -> None:
    """Time-stretch in_wav to target_dur using pyrubberband or librosa."""
    try:
        import librosa
        import soundfile as sf
        import numpy as np
        y, sr = librosa.load(str(in_wav), sr=None, mono=True)
        cur_dur = len(y) / sr
        if target_dur <= 0 or abs(cur_dur - target_dur) < 0.05:
            shutil.copy2(in_wav, out_wav)
            return
        rate = cur_dur / target_dur
        rate = float(np.clip(rate, 0.7, 1.3))
        try:
            import pyrubberband as pyrb
            y2 = pyrb.time_stretch(y, sr, rate)
        except Exception:
            y2 = librosa.effects.time_stretch(y, rate=rate)
        sf.write(str(out_wav), y2, sr)
    except Exception as e:
        print(f"[dub_song] Stretch failed: {e} — copying unchanged", flush=True)
        shutil.copy2(in_wav, out_wav)


def concat_wavs(wav_paths: list[Path], out_path: Path) -> None:
    """Concatenate WAV files via FFmpeg."""
    if not wav_paths:
        return
    if len(wav_paths) == 1:
        shutil.copy2(wav_paths[0], out_path)
        return

    # Build FFmpeg concat filter — args list, no shell
    inputs = []
    for w in wav_paths:
        inputs += ["-i", str(w)]
    concat_filter = f"{''.join(f'[{i}:a]' for i in range(len(wav_paths)))}concat=n={len(wav_paths)}:v=0:a=1[out]"
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", concat_filter,
        "-map", "[out]", str(out_path),
    ]
    result = subprocess.run(cmd, shell=False, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr.decode()[-500:]}")


def mix_over_instrumental(dub_wav: Path, instrumental_wav: Path,
                          start_s: float, end_s: float,
                          out_path: Path, bg_vol: float = 0.2) -> None:
    """
    Overlay dub_wav on top of instrumental_wav starting at start_s.
    Only the [start_s, end_s] window of the instrumental is used.
    """
    delay_ms = int(start_s * 1000)
    dur = max(0.1, end_s - start_s)
    # CORRECT: args list for FFmpeg — never shell=True
    cmd = [
        "ffmpeg", "-y",
        "-i", str(instrumental_wav),
        "-i", str(dub_wav),
        "-filter_complex",
        f"[0]atrim=start={start_s}:end={end_s},asetpts=PTS-STARTPTS,volume={bg_vol}[bg];"
        f"[1]adelay={delay_ms}|{delay_ms}[dub];"
        f"[bg][dub]amix=inputs=2:duration=longest:normalize=0[out]",
        "-map", "[out]",
        "-t", str(dur + 2),  # slight buffer
        str(out_path),
    ]
    result = subprocess.run(cmd, shell=False, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg mix failed: {result.stderr.decode()[-500:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job_dir",         required=True)
    ap.add_argument("--segment",         required=True)
    ap.add_argument("--path_mode",       default="A", choices=["A", "B"])
    ap.add_argument("--show",            default="")
    ap.add_argument("--characters_root", default="characters")
    ap.add_argument("--is_series",       default="false")
    args = ap.parse_args()

    job_dir   = Path(args.job_dir)
    segment   = args.segment
    path_mode = args.path_mode
    show      = args.show
    chars_root = Path(args.characters_root)
    is_series  = args.is_series.lower() in ("true", "1", "yes")
    ep        = job_dir.name

    write_status(job_dir, segment, {
        "stage": f"song_{segment}", "status": "processing",
        "progress": 0, "error": None, "logs": [],
    })

    # Acquire GPU lock
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if GPU_LOCK.exists():
        holder = GPU_LOCK.read_text(encoding="utf-8").strip()
        print(f"[dub_song] GPU busy: {holder}", file=sys.stderr, flush=True)
        sys.exit(2)

    GPU_LOCK.write_text(f"dub_song_{segment}:{ep}", encoding="utf-8")

    model = None
    try:
        state_path = job_dir / "state_director.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        source_lang = state.get("source_lang", "ja")

        song_entry = next(
            (s for s in state.get("songs", []) if s.get("segment") == segment),
            None,
        )
        if song_entry is None:
            raise ValueError(f"Song segment '{segment}' not found in state_director.json")

        start_s = float(song_entry.get("start", 0))
        end_s   = float(song_entry.get("end", start_s + 60))
        artist  = song_entry.get("artist", "artist_unknown").lower().replace(" ", "_")

        iso_dir = Path("workspace") / "2_isolated" / ep
        instrumental = iso_dir / "no_vocals.wav"
        if not instrumental.exists():
            instrumental = iso_dir / "instrumental.wav"
        if not instrumental.exists():
            raise FileNotFoundError(f"Instrumental not found at {iso_dir}")

        songs_dir = job_dir / "songs"
        songs_dir.mkdir(parents=True, exist_ok=True)

        if path_mode == "B":
            # Path B: human guide vocal → RVC
            guide_wav = songs_dir / f"{segment}_guide.wav"
            if not guide_wav.exists():
                raise FileNotFoundError(
                    f"Guide vocal not found: {guide_wav}\n"
                    "Place a guide vocal WAV at that path to use Path B."
                )
            # RVC conversion (model must exist in character's rvc_model/ dir)
            artist_dir = chars_root / "shows" / show / artist
            rvc_model_dir = artist_dir / "rvc_model"
            if not rvc_model_dir.exists():
                raise FileNotFoundError(
                    f"RVC model not found at {rvc_model_dir} — train it first."
                )
            # RVC integration is project-specific; call via subprocess args list
            rvc_out = songs_dir / f"{segment}_rvc_out.wav"
            rvc_script = Path("python_backend/rvc_infer.py")
            if not rvc_script.exists():
                raise FileNotFoundError("python_backend/rvc_infer.py not found — add RVC integration")
            subprocess.run([
                "python", str(rvc_script),
                "--guide", str(guide_wav),
                "--model_dir", str(rvc_model_dir),
                "--output", str(rvc_out),
            ], check=True, shell=False)

            final_dub = songs_dir / f"{segment}_dubbed_standard.wav"
            mix_over_instrumental(rvc_out, instrumental, start_s, end_s, final_dub)

        else:
            # Path A: Gemini translation → IndexTTS2 → Rubberband → mix
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not set — needed for song translation")

            # Extract source lyrics from whisper segments in the song's time window
            whisper_path = job_dir / "state_whisper.json"
            segments_all: list[dict] = []
            if whisper_path.exists():
                segments_all = json.loads(whisper_path.read_text(encoding="utf-8"))

            lyric_input = [
                {"line_index": i, "start": s["start"], "end": s["end"],
                 "source_text": s["text"]}
                for i, s in enumerate(segments_all)
                if s.get("start", 0) >= start_s and s.get("end", 0) <= end_s
            ]
            if not lyric_input:
                log(job_dir, segment, "WARNING: no whisper segments in song window — skipping")
                write_status(job_dir, segment, {"status": "done", "progress": 100,
                                                "dubbed_wav": "", "note": "no segments found"})
                return

            log(job_dir, segment, f"Translating {len(lyric_input)} lyric lines via Gemini")
            translated = gemini_lyric_translation(lyric_input, source_lang, api_key)

            # Update state with lyrics
            for s in state.get("songs", []):
                if s.get("segment") == segment:
                    s["lyrics_english"] = " ".join(
                        t.get("lyrics_english", "") for t in translated
                    )
            save_state(state_path, state)

            write_status(job_dir, segment, {"progress": 40})

            # Load IndexTTS2
            log(job_dir, segment, "Loading IndexTTS2 for song synthesis")
            try:
                from indextts.infer import IndexTTS
                model = IndexTTS(
                    model_dir="model/IndexTTS2",
                    cfg_path="model/IndexTTS2/config.yaml",
                )
            except Exception as e:
                raise ImportError(f"IndexTTS2 not available: {e}")

            # Find artist ref wav
            artist_dir = chars_root / "shows" / show / artist
            if not artist_dir.exists():
                artist_dir = chars_root / "global_roster" / artist
            ref_wav = next(artist_dir.glob("ref_*.wav"), None) if artist_dir.exists() else None
            if not artist_dir.exists() or ref_wav is None:
                ref_wav = next(chars_root.glob("**/seeds/*.wav"), None)
                if ref_wav is None:
                    raise FileNotFoundError(
                        f"No ref wav for artist '{artist}' — add seeds under {artist_dir}"
                    )

            log(job_dir, segment, f"Synthesizing with ref: {ref_wav}")
            lyric_wavs = synthesize_lyrics_indextts(
                translated, ref_wav,
                songs_dir / f"{segment}_lyric_wavs",
                model,
            )
            write_status(job_dir, segment, {"progress": 70})

            # Stretch each line to its slot, then concatenate
            fit_wavs: list[Path] = []
            for i, (line_data, lyric_wav) in enumerate(zip(lyric_input, lyric_wavs)):
                target_dur = float(line_data["end"]) - float(line_data["start"])
                fit_out = songs_dir / f"{segment}_fit_{i:03d}.wav"
                rubberband_fit(lyric_wav, fit_out, target_dur)
                fit_wavs.append(fit_out)

            concat_out = songs_dir / f"{segment}_concat.wav"
            concat_wavs(fit_wavs, concat_out)

            write_status(job_dir, segment, {"progress": 85})

            final_dub = songs_dir / f"{segment}_dubbed_standard.wav"
            mix_over_instrumental(concat_out, instrumental, start_s, end_s, final_dub)

        # Update state_director.json
        for s in state.get("songs", []):
            if s.get("segment") == segment:
                s["dubbed_wav"]  = str(final_dub)
                s["status"]      = "song_complete"
        save_state(state_path, state)

        write_status(job_dir, segment, {
            "status": "done", "progress": 100,
            "dubbed_wav": str(final_dub),
        })
        log(job_dir, segment, f"Song dubbed → {final_dub}")

        # Copy to vault if series song
        if is_series and path_mode == "A":
            vault_dir = chars_root / "shows" / show / "songs"
            vault_dir.mkdir(parents=True, exist_ok=True)
            vault_path = vault_dir / f"{segment}_standard.wav"
            shutil.copy2(final_dub, vault_path)
            log(job_dir, segment, f"Saved to song vault → {vault_path}")

            for s in state.get("songs", []):
                if s.get("segment") == segment:
                    s["song_source"] = "cache"
                    s["vault_wav"]   = str(vault_path)
            save_state(state_path, state)

    except Exception as e:
        traceback.print_exc()
        write_status(job_dir, segment, {"status": "error", "error": str(e)})
        sys.exit(1)
    finally:
        if model is not None:
            try:
                del model
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
        if GPU_LOCK.exists():
            try:
                GPU_LOCK.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
