# Yanflix Dubbing Studio — Master Specification (v4)

**This is the single source of truth for architecture, data schema, API contracts, and constraints.**
Workflow node-by-node specs live in separate files — hand Claude Code the master spec PLUS whichever workflow file it needs to build.

**Companion files:**
- `yanflix_workflow_0.md` — WF0: Remote Isolate via RunPod (entry point for autopilot)
- `yanflix_workflow_1.md` — WF1: Character Vault & Voice Bank
- `yanflix_workflow_2.md` — WF2: Gemini Script Director
- `yanflix_workflow_3.md` — WF3: Dub Pipeline (Standard + AAVE)

---

## What Changed v3 → v4

| Area | Before | After |
|---|---|---|
| Gemini model | gemini-2.0-flash | gemini-2.5-flash ONLY (2.0 at quota) |
| Transcription | Local Faster-Whisper | Groq Whisper chunked (cloud, ~30s) |
| Speaker ID | Gemini Director call | Groq llama-3.3-70b → Gemini fallback |
| Translation | Gemini | Groq → Gemini fallback |
| Demucs | Local GPU only | RunPod L4 via WF0 (laptop GPU freed) |
| pyannote | Local GPU (caused WF1 freeze) | RunPod L4 only (when use_pyannote: true) |
| GPU lock for transcribe | Always required | Only for local Faster-Whisper fallback |
| Autopilot entry point | WF1 webhook | WF0 webhook (WF0 → WF1 → WF2 → WF3) |
| n8n file handling | n8n passed binary data | Next.js owns ALL file I/O, n8n orchestrates only |
| Show slug | Derived from show_name | ALWAYS explicit ASCII field in every payload |
| ElevenLabs cap | Global only | Per-show cap via credit_log.json |

---

## 0. Hard Constraints

### 0.1 Cost & Cloud APIs

**Groq** is the primary cloud engine. Zero local VRAM impact.

- **Confirmed working models (June 2026):**
  - Chat: `llama-3.3-70b-versatile` (primary), `llama-3.1-8b-instant`, `meta-llama/llama-4-scout-17b-16e-instruct`, `compound-beta`, `compound-beta-mini`
  - Audio: `whisper-large-v3-turbo` (primary), `whisper-large-v3`
- **Gemini:** `gemini-2.5-flash` ONLY. `gemini-2.0-flash` and `gemini-2.0-flash-lite` are at quota. Do not reference them anywhere in code.
- **ElevenLabs confirmed models:** `eleven_multilingual_v2`, `eleven_turbo_v2_5`, `eleven_flash_v2_5`
- **ElevenLabs balance:** 130,889 credits. Per-show cap default 2,000 (configurable). ~700 credits per character bank.

**LLM cascade for all routes:**
1. `groq/llama-3.3-70b-versatile`
2. `gemini-2.5-flash` (GEMINI_API_KEY)
3. `gemini-2.5-flash` (GEMINI_API_KEY_2 — second key)
4. `ollama/llama3.1:8b` — ONLY if `jobs/gpu.lock` absent

**MANDATORY before every JSON.parse():**
```typescript
text.replace(/```json/g,'').replace(/```/g,'').trim()
```
Apply to Groq, Gemini, and Ollama responses without exception.

**Batching is mandatory.** Never call any LLM per-line. One call per episode for diarize, one for translate, one per song for lyrics.

### 0.2 GPU Strategy

**Local:** RTX 4050, 6GB VRAM. ONE job at a time, enforced by `jobs/gpu.lock`.
**Remote:** RunPod L4 GPU Pod for Demucs (always when `RUNPOD_ENABLED=true`) and pyannote (only when `use_pyannote: true`).

**GPU lock rules:**
- Routes CHECK the lock → return HTTP 409 if held
- Routes NEVER WRITE the lock
- Python workers WRITE and DELETE the lock in a `finally` block
- Groq transcription does NOT use the GPU lock
- RunPod jobs do NOT use the local GPU lock

**Per-worker pattern:**
```python
# Write gpu.lock at start
try:
    load model → process → del model → torch.cuda.empty_cache()
finally:
    delete gpu.lock  # always, even on crash
```

### 0.3 All State Lives on Disk

`jobs/{ep_folder}/status_{stage}.json` per stage. `state_director.json` is the master per-line database, written after every line. No in-memory job maps. Crash anywhere → rerun resumes from last completed line.

### 0.4 Two Clients, One Backend

- **Next.js API (port 3000, Windows host):** the only backend. Spawns Conda workers, owns filesystem, holds all credentials (R2, RunPod, ElevenLabs).
- **Yanflix UI:** calls Next.js `/api/*` directly.
- **n8n (Docker, port 5678):** calls same Next.js API via `http://host.docker.internal:3000`. Orchestrates timing only — n8n NEVER handles files, never holds API keys, never reads/writes local disk.
- **Docker scope:** n8n ONLY. No GPU workloads, no Python workers, no Next.js in Docker.

### 0.5 Multi-Language, Multi-Show Rules

**`show_slug` is ALWAYS explicit ASCII. Never derived from show_name.**

```
"사랑의 불시착"  → show_slug: "crash_landing"
"请回答1988"    → show_slug: "reply_1988"
"進撃の巨人"    → show_slug: "attack_on_titan"
"Smoking Behind the Supermarket" → show_slug: "smoking_supermarket"
```

`ep_folder` = `{show_slug}_{episode_id}` e.g. `crash_landing_s01e01`

**Every webhook payload carries all 6 fields:**
```json
{
  "show_name":    "사랑의 불시착",
  "show_slug":    "crash_landing",
  "episode_id":   "s01e01",
  "ep_folder":    "crash_landing_s01e01",
  "source_lang":  "ko",
  "raw_file_name":"crash_landing_s01e01.mp4"
}
```

`source_lang` codes: `ja` (Japanese), `ko` (Korean), `zh` (Mandarin), `en` (English).
Passed verbatim to Groq Whisper. **Never hardcode `"ja"` anywhere.**

All filesystem paths use `show_slug` — no Unicode in paths, R2 keys, or ChromaDB IDs.

---

## 1. System Architecture

```
   ┌──────────────────────┐     ┌──────────────────────────────┐
   │   Yanflix UI (React) │     │   n8n Autopilot (Docker)     │
   │   8-stage Dub Studio │     │   WF0 → WF1 → WF2 → WF3     │
   └──────────┬───────────┘     └──────────────┬───────────────┘
              │ fetch /api/*                   │ http://host.docker.internal:3000
              ▼                                ▼
   ┌──────────────────────────────────────────────────────────────┐
   │         Next.js Native API (port 3000, Windows host)         │
   │  Spawns Conda workers · owns disk · holds all credentials    │
   └───────────────────────┬──────────────────────────────────────┘
               ┌───────────┴────────────┐
               ▼                        ▼
   ┌───────────────────────┐   ┌────────────────────────────────┐
   │  Local RTX 4050 6GB   │   │  RunPod L4 GPU Pod             │
   │  IndexTTS2, RVC,      │   │  Demucs + pyannote             │
   │  Rubberband, NISQA,   │   │  Files transit via R2          │
   │  ChromaDB, Pydub      │   │  Result → Next.js → local disk │
   └───────────────────────┘   └────────────────────────────────┘
   External APIs (Next.js calls directly, never n8n):
     Groq · Gemini 2.5 Flash · ElevenLabs · Cloudflare R2 · RunPod
```

### Autopilot Chain
```
UI "Autopilot" button
  → POST http://localhost:5678/webhook/runpod-isolate   (WF0)
      → Demucs on RunPod L4
      → POST http://host.docker.internal:5678/webhook/harvest-characters  (WF1)
          → segment_lines + NISQA + ElevenLabs banks
          → POST http://host.docker.internal:5678/webhook/script-director  (WF2)
              → Groq Whisper transcribe + Groq Director diarize
              → PAUSE: human reviews cast in UI
              → human clicks "Save Cast" → save_cast API → resumes WF2
              → POST http://host.docker.internal:5678/webhook/run-dub  (WF3)
                  → translate + songs + synth + fit + render
```

---

## 2. API Routes

| Route | Method | Purpose | GPU |
|---|---|---|---|
| `/api/status` | GET | Merged stage statuses + gpu.lock + runpod_job_id | – |
| `/api/gpu-lock` | DELETE | Remove stale lock after worker crash | – |
| `/api/characters` | GET | Character Vault listing from disk + ChromaDB | – |
| `/api/ytdlp` | POST | Download source via yt-dlp into `0_raw_videos/` | – |
| `/api/r2-upload` | POST | FFmpeg-extract audio → stream upload to R2 → return r2_key | – |
| `/api/runpod-submit` | POST | Submit async job to RunPod → return job_id | – |
| `/api/runpod-poll` | GET | Poll RunPod status → normalized done/processing/error | – |
| `/api/r2-fetch-stems` | POST | Download R2 outputs → local stable paths → delete R2 keys → mark isolate done | – |
| `/api/isolate` | POST | Demucs (local fallback only; RunPod path uses routes above) | ✓ local |
| `/api/transcribe` | POST | Groq Whisper chunked (primary) or Faster-Whisper (fallback) | (✓ fallback) |
| `/api/diarize` | POST | Groq/Gemini Director call; pyannote via RunPod if `use_pyannote:true` | (✓ RunPod) |
| `/api/save_cast` | POST | Lock cast assignments into `state_director.json` | – |
| `/api/save_speaker_to_vault` | POST | NISQA-gated seeds → ChromaDB embedding | – |
| `/api/clone_speakers` | POST | ElevenLabs bank builder; skips ChromaDB-known characters | – |
| `/api/translate` | POST | Batched Groq/Gemini dual-translation (standard + AAVE) | – |
| `/api/direct` | POST | Batched emotion-tag / rewrite pass | – |
| `/api/save_segments` | POST | Persist script-table edits into `state_director.json` | – |
| `/api/actor` | POST | IndexTTS2 batch synthesis; resumable | ✓ |
| `/api/regen_line` | POST | Re-synthesize one line | ✓ |
| `/api/regen_speaker` | POST | Re-synthesize all lines for one character | ✓ |
| `/api/voice_test` | POST | Test sentence through IndexTTS2 | ✓ |
| `/api/fit_audio` | POST | Rubberband time-stretch (CPU) | – |
| `/api/dub_song` | POST | Song pipeline Path A or B | ✓ |
| `/api/render` | POST | FFmpeg final mux → `5_outputs/{ep}_{track}.mp4` | – |

**GPU route pattern:**
validate → check gpu.lock (409 if held) → spawn Conda worker detached → return `{status:"processing"}`.
Worker writes status file, deletes lock in `finally`.

**RunPod routes** (`/api/r2-upload`, `/api/runpod-submit`, `/api/runpod-poll`, `/api/r2-fetch-stems`): no GPU lock, no Conda spawn. Called by n8n via Next.js — all credentials stay on Next.js server.

---

## 3. Emotion Enum (Fixed)

```
neutral | cheerful | angry | sad | whisper | exhausted | excited | fearful
```

Used by: Groq/Gemini Director prompt output, ElevenLabs bank filenames (`ref_{emotion}.wav`), UI dropdown, IndexTTS2 ref lookup. Fallback always `ref_neutral.wav`. Never crash on missing emotion file.

---

## 4. Pipeline Stages

### Stage 3 — Isolate (Demucs)
- **RunPod mode** (`RUNPOD_ENABLED=true`): WF0 handles via R2. No local GPU lock.
- **Local mode** (`RUNPOD_ENABLED=false`): `isolate.py` in Conda env `sonitr`, GPU-locked.
- **Both modes:** Demucs nests output as `htdemucs/{basename}/vocals.wav`. Worker copies to stable paths:
  - `workspace/2_isolated/{ep_folder}/vocals.wav`
  - `workspace/2_isolated/{ep_folder}/instrumental.wav`

### Stage 4 — Speakers (Hybrid)
1. `/api/transcribe` → Groq Whisper chunked (primary). Fallback: local Faster-Whisper medium int8.
2. `/api/diarize` → one Groq batch call (Director prompt) → returns per-line speaker proposals + confidence + song segments.
3. Human reviews cast board in UI. Corrects assignments.
4. `/api/save_cast` → locks cast into `state_director.json`. Stage not done until cast saved.
- `use_pyannote: true`: pyannote runs on RunPod L4 only (never local). Clusters fed as hints to Groq. Default OFF.
- After cast lock: `segment_lines.py` (CPU, Pydub) slices `vocals.wav` into per-line clips + song segments.

### Stage 5 — Script
- `/api/translate`: one Groq batch → `text_standard` + `text_aave` for every line.
- `/api/direct`: emotion-tag / natural-rewrite pass. Independently regenerable.

### Song Modes
- **Cache** (recurring series songs): dubbed once in Episode 1, saved to `characters/shows/{show_slug}/songs/{segment}_{track}.wav`. All future episodes read from vault — zero GPU, zero credits.
- **Generate** (films, unique songs): full Path A ($0 — Groq singable translation → IndexTTS2 → Rubberband) or Path B (human guide vocal → RVC).

### Stage 6 — Voices
1. NISQA-rank line clips per character → top 3–5 seeds (≥2s, MOS ≥ threshold).
2. Resemblyzer embedding → ChromaDB upsert. Known characters with complete banks = 0 credits on repeat episodes.
3. `/api/clone_speakers`: new characters only → ElevenLabs IVC clone → 7 TTS calls (one per emotion) → `ref_{emotion}.wav`. Check per-show credit cap before every call.
4. `use_direct_seeds: true` (config flag): skip ElevenLabs, use original-language seeds as IndexTTS2 refs.

### Stages 7–8 — Synthesis, Fit, Render
- **IndexTTS2** (`synthesize_dub.py`): load once, batch all lines, write `state_director.json` per line, free VRAM.
- **Rubberband** (`audio_fitter.py`): stretch α clamped [0.7, 1.3]. Runs CPU only.
- **FFmpeg** (`render_video.py`): video stream copied (no re-encode). Audio bed = `instrumental.wav` at `bgVol` + fitted lines overlaid. Songs placed at timestamps. Output: `workspace/5_outputs/{ep_folder}_{track_mode}.mp4`.

---

## 5. Master Data Schema — `state_director.json`

```json
{
  "ep_folder":   "crash_landing_s01e01",
  "show_name":   "사랑의 불시착",
  "show_slug":   "crash_landing",
  "source_lang": "ko",
  "cast_locked": true,
  "characters": {
    "se_ri": { "bank_dir": "characters/shows/crash_landing/se_ri", "bank_complete": true, "chroma_id": "..." }
  },
  "songs": [
    {
      "segment": "intro", "artist": "artist_x",
      "start": 0.0, "end": 90.0,
      "song_source": "generate",
      "lyrics_english": "", "path_mode": "A",
      "dubbed_wav": "", "vault_wav": "", "status": "pending"
    }
  ],
  "lines": [
    {
      "line_index": 0,
      "start": 12.5, "end": 15.2,
      "character": "se_ri",
      "speaker_confidence": 0.91,
      "type": "speech",
      "detected_emotion": "fearful",
      "source_text": "여기가 어디야?",
      "text_standard": "Where am I?",
      "text_aave": "Where is this place?",
      "clip_path": "jobs/crash_landing_s01e01/line_clips/line_000.wav",
      "audio_synthesis_status": { "standard": "pending", "aave": "pending" },
      "audio_fit_status":       { "standard": "pending", "aave": "pending" },
      "raw_wav":                { "standard": "", "aave": "" },
      "fit_wav":                { "standard": "", "aave": "" },
      "synthesis_quality":      { "standard": "pending", "aave": "pending" },
      "mos_score":              { "standard": null, "aave": null },
      "error_msg": null
    }
  ]
}
```

All workers preserve unknown fields. Per-line statuses: `pending|done|error`.

---

## 6. LLM Integration

### LLM Cascade (implement in every route that calls an LLM)

```typescript
async function callLLM(prompt: string, systemPrompt: string): Promise<string> {
  // 1. Groq — fastest, confirmed working
  try {
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: { "Authorization": `Bearer ${process.env.GROQ_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile", temperature: 0.1,
        messages: [{ role: "system", content: systemPrompt }, { role: "user", content: prompt }]
      })
    });
    if (res.ok) return stripMarkdown((await res.json()).choices[0].message.content);
  } catch(e) {}

  // 2. Gemini 2.5 Flash — key 1
  try {
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${process.env.GEMINI_API_KEY}`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contents: [{ parts: [{ text: systemPrompt + "\n\n" + prompt }] }] }) }
    );
    if (res.ok) return stripMarkdown((await res.json()).candidates[0].content.parts[0].text);
  } catch(e) {}

  // 3. Gemini 2.5 Flash — key 2
  try { /* same with GEMINI_API_KEY_2 */ } catch(e) {}

  // 4. Ollama — last resort, only if GPU free
  if (!fs.existsSync("./jobs/gpu.lock")) {
    const res = await fetch("http://localhost:11434/api/generate", {
      method: "POST",
      body: JSON.stringify({ model: "llama3.1:8b", prompt, stream: false, format: "json" })
    });
    return stripMarkdown((await res.json()).response);
  }

  throw new Error("All LLM providers failed or GPU busy");
}

function stripMarkdown(text: string): string {
  return text.replace(/```json/g, '').replace(/```/g, '').trim();
}
```

### Groq Whisper Chunked Transcription

```python
CHUNK_SECONDS = 110  # ~110s at 256kbps ≈ 21MB — under Groq's 25MB limit

def transcribe_with_groq(vocals_path, source_lang):  # source_lang = "ko", "zh", "ja", "en"
    audio = AudioSegment.from_wav(vocals_path)
    chunks, offset = [], 0.0
    for start_ms in range(0, len(audio), CHUNK_SECONDS * 1000):
        chunk = audio[start_ms : start_ms + CHUNK_SECONDS * 1000]
        tmp = f"/tmp/chunk_{start_ms}.wav"
        chunk.export(tmp, format="wav")
        with open(tmp, "rb") as f:
            response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("chunk.wav", f, "audio/wav")},
                data={"model": "whisper-large-v3-turbo", "language": source_lang,
                      "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
            )
        for seg in response.json().get("segments", []):
            seg["start"] += offset; seg["end"] += offset
            chunks.append(seg)
        offset += len(chunk) / 1000
        os.remove(tmp)
    return chunks
```

---

## 7. n8n Workflow Summary

4 workflows total. n8n in Docker on port 5678.

| Workflow | Webhook path | Triggered by | Triggers |
|---|---|---|---|
| WF0: Remote Isolate | `runpod-isolate` | UI Autopilot button | WF1 |
| WF1: Character Vault | `harvest-characters` | WF0 (or UI manually) | WF2 |
| WF2: Script Director | `script-director` | WF1 (or UI manually) | WF3 |
| WF3: Dub Pipeline | `run-dub` | WF2 (or UI manually) | — |

**All HTTP Request nodes:** `http://host.docker.internal:3000`
**n8n never handles files or credentials** — all R2/RunPod/ElevenLabs calls go through Next.js routes.

**DELETE these old workflows if they exist:**
- "Yanflix ◆ WF3: Voice Generation & Audio Fitting"
- "Yanflix ◆ WF4: Cinematic Video Compositing"

### `/api/status` Response Contract

```json
{
  "status_isolate":         "done|processing|error|offline",
  "status_transcribe":      "done|processing|error|offline",
  "status_segment":         "done|processing|error|offline",
  "status_harvest":         "done|processing|error|offline",
  "status_clone":           "done|processing|error|offline",
  "status_translate":       "done|processing|error|offline",
  "status_song_intro":      "done|processing|error|offline",
  "status_song_outro":      "done|processing|error|offline",
  "status_synth_standard":  "done|processing|error|offline",
  "status_synth_aave":      "done|processing|error|offline",
  "status_fit_standard":    "done|processing|error|offline",
  "status_fit_aave":        "done|processing|error|offline",
  "status_render_standard": "done|processing|error|offline",
  "status_render_aave":     "done|processing|error|offline",
  "gpu_lock_holder":        "stage:ep_folder or null",
  "runpod_job_id":          "string or null"
}
```

Missing status file = `"offline"`. Worker filenames must match exactly: `status_synth_standard.json`, `status_fit_aave.json`, etc.

---

## 8. File System Layout

```
yanflix-dubbing-studio/
├── .env.local
├── app/
│   ├── page.tsx
│   ├── globals.css
│   └── api/                        ← all routes from Section 2
├── characters/
│   ├── global_roster/
│   │   ├── generic_male_01/        ← ref_{emotion}.wav ×8 + profile.json
│   │   └── generic_female_01/
│   └── shows/{show_slug}/
│       ├── credit_log.json         ← ElevenLabs spend tracker per show
│       ├── songs/                  ← Song vault (dubbed once, reused every episode)
│       │   ├── intro_standard.wav
│       │   ├── intro_aave.wav
│       │   ├── outro_standard.wav
│       │   └── outro_aave.wav
│       └── {character}/
│           ├── seeds/              ← NISQA-gated top clips
│           ├── ref_{emotion}.wav   ← ×8 emotions
│           ├── profile.json
│           └── rvc_model/          ← artists only
├── voice_registry/chroma/          ← ChromaDB persistent store
├── jobs/
│   ├── gpu.lock                    ← transient, deleted by worker finally
│   └── {ep_folder}/
│       ├── status_{stage}.json
│       ├── state_director.json
│       ├── line_clips/line_NNN.wav
│       ├── songs/
│       └── tts_audio/{standard,aave}/{raw,fit}_line_NNN.wav
├── prompts/
│   ├── 01_script_director.md
│   ├── 02_dual_translation.md
│   └── 03_song_translation.md
├── python_backend/
│   ├── isolate.py
│   ├── transcribe.py
│   ├── segment_lines.py
│   ├── harvest_voices.py
│   ├── build_voice_bank.py
│   ├── synthesize_dub.py
│   ├── audio_fitter.py
│   ├── dub_song.py
│   └── render_video.py
└── workspace/
    ├── 0_raw_videos/
    ├── 1_inputs/
    ├── 2_isolated/
    ├── 3_transcripts/
    └── 5_outputs/
```

---

## 9. UI Changes Required

1. **LLM Settings panel:** Groq API key (primary), Gemini 2.5 Flash key (fallback). Remove all `gemini-2.0-flash` references.
2. **Autopilot button:** POST to `http://localhost:5678/webhook/runpod-isolate` (WF0), not `harvest-characters`.
3. **Speakers stage:** cast board shows Groq Director proposals with confidence badges. pyannote toggle = OFF by default; when ON, note it runs on RunPod.
4. **Voices stage:** per-character bank status grid (8 emotions), "Build Bank" button with credit estimate, "From Vault ✓" badge for ChromaDB-known characters.
5. **Script stage:** Standard/AAVE text toggle.
6. **Export stage:** track selector (Standard / AAVE / both) per run.
7. **Autopilot pill:** show "AUTOPILOT" when `owner=="n8n"`. Disable UI buttons while gpu.lock held. "Waiting for cast review" banner when autopilot is paused at cast-lock gate.
8. **System status footer:** add RunPod indicator (green = reachable, grey = disabled).
9. Replace fake `sysCheck` with real `/api/status` check: ffmpeg, groq key, gemini key, elevenlabs key, chromadb, runpod endpoint.

---

## 10. Confirmed Bugs — Fix Before Anything Else

**Bug 1:** WF1 trigger was "When clicking Execute workflow" not a webhook. Fix: replace with Webhook node path `harvest-characters`. Activate WF1.

**Bug 2:** WF2 named wrong. Fix: rename to exactly `Yanflix ◆ WF2: Gemini Script Director`. Confirm webhook path `script-director` is active.

**Bug 3:** Demucs fails on filenames with brackets/spaces. Fix: NEVER use `shell=True` with user-supplied paths. Always use args list:
```python
subprocess.run(["demucs", "--two-stems=vocals", str(video_path), "-o", str(output_dir)], check=True, shell=False)
```
Applies to ALL workers and ALL `spawn()` calls in Next.js routes.

**Bug 4:** Source MP4 stored in wrong folder. Fix: MP4 → `0_raw_videos/`. Extracted audio → `1_inputs/{ep_folder}_audio.wav`. Never swap these.

**Bug 5 (n8n):** `specifyBody: "json"` doesn't evaluate `={{ }}` expressions. Fix: use `contentType: "json"` + `bodyParameters` array. Always set `sendBody: true`.

**Bug 6 (n8n):** `$node['Name']` inside Python f-strings drops prefix. Fix: build n8n expression strings with concatenation only.

---

## 11. Subprocess Safety Rule

**No Python file in `python_backend/` may use `shell=True` with any path from user input or filesystem.**

```python
# WRONG
subprocess.run(f'demucs "{video_path}"', shell=True)

# CORRECT
subprocess.run(["demucs", "--two-stems=vocals", str(video_path), "-o", str(out_dir)], shell=False, check=True)
```

---

## 12. RunPod Handler (`python_backend/runpod_handler.py`)

Deployed via `flash deploy`. Runs on RunPod L4 GPU Pod.

```python
import runpod, os, boto3, tempfile, subprocess, torch, torchaudio
import torchaudio.transforms as T
from botocore.config import Config

r2 = boto3.client("s3",
    endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    config=Config(signature_version="s3v4"), region_name="auto"
)
BUCKET = os.environ["R2_BUCKET"]

def handler(job):
    inp = job["input"]
    task, r2_key, ep_folder = inp["task"], inp["r2_key"], inp["ep_folder"]

    with tempfile.TemporaryDirectory() as tmp:
        local_input = os.path.join(tmp, "input.wav")
        r2.download_file(BUCKET, r2_key, local_input)
        r2.delete_object(Bucket=BUCKET, Key=r2_key)

        if task == "isolate":
            out_dir = os.path.join(tmp, "separated")
            subprocess.run(["demucs", "--two-stems=vocals", "--out", out_dir, local_input], check=True, shell=False)
            import glob
            vocals = glob.glob(f"{out_dir}/**/vocals.wav", recursive=True)[0]
            no_vocals = glob.glob(f"{out_dir}/**/no_vocals.wav", recursive=True)[0]
            r2.upload_file(vocals,    BUCKET, f"{ep_folder}/vocals.wav")
            r2.upload_file(no_vocals, BUCKET, f"{ep_folder}/instrumental.wav")
            return {"vocals_key": f"{ep_folder}/vocals.wav", "instrumental_key": f"{ep_folder}/instrumental.wav"}

        elif task == "diarize":
            from pyannote.audio import Pipeline
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1", token=os.environ["HF_TOKEN"]
            ).to(torch.device("cuda"))
            waveform, sr = torchaudio.load(local_input)
            if waveform.shape[0] > 1:
                waveform = waveform.mean(dim=0, keepdim=True)
            if sr != 16000:
                waveform = T.Resample(orig_freq=sr, new_freq=16000)(waveform)
            result = pipeline({"waveform": waveform, "sample_rate": 16000})
            # CONFIRMED WORKING — must access .speaker_diarization attribute
            return {"segments": [
                {"start": round(t.start, 3), "end": round(t.end, 3), "speaker": s}
                for t, _, s in result.speaker_diarization.itertracks(yield_label=True)
            ]}

runpod.serverless.start({"handler": handler})
```

**requirements.txt (RunPod):**
```
demucs
pyannote.audio
torch
torchaudio
boto3
runpod
```

---

## 13. Environment Variables (`.env.local`)

```bash
# LLM / AI
GROQ_API_KEY=gsk_81zW...
GEMINI_API_KEY=AIzaSyAo...
GEMINI_API_KEY_2=AQ.Ab8RN...
ELEVENLABS_API_KEY=7f08be4b...
ELEVENLABS_CAP_PER_SHOW=2000

# n8n
N8N_BASE_URL=http://localhost:5678

# ChromaDB
CHROMA_HOST=localhost
CHROMA_PORT=8000

# RunPod
RUNPOD_ENABLED=true
RUNPOD_API_KEY=
RUNPOD_ENDPOINT_ID=

# Cloudflare R2 (required when RUNPOD_ENABLED=true)
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=yanflix-audio

# Paths
CHARACTERS_ROOT=./characters
WORKSPACE_ROOT=./workspace
```

---

## 14. Confirmed Model Availability (June 2026)

| Provider | Model | Status |
|---|---|---|
| Groq | llama-3.3-70b-versatile | ✅ |
| Groq | llama-3.1-8b-instant | ✅ |
| Groq | meta-llama/llama-4-scout-17b-16e-instruct | ✅ |
| Groq | compound-beta / compound-beta-mini | ✅ |
| Groq | whisper-large-v3-turbo | ✅ |
| Groq | whisper-large-v3 | ✅ |
| Gemini | gemini-2.5-flash | ✅ |
| Gemini | gemini-2.5-pro | ⚠ quota |
| Gemini | gemini-2.0-flash | ⚠ quota |
| Gemini | gemini-2.0-flash-lite | ⚠ quota |
| ElevenLabs | eleven_multilingual_v2 | ✅ |
| ElevenLabs | eleven_turbo_v2_5 / eleven_flash_v2_5 | ✅ |
| Ollama | llama3.1:8b | ✅ local only |

**Do not reference gemini-2.0-flash or gemini-2.0-flash-lite anywhere in code.**
