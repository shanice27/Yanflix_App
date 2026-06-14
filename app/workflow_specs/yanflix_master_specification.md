# Yanflix Dubbing Studio: Master Specification (v3 — FINAL)

**Purpose:** Hand this document to Claude Code together with `Yanflix.html` (the existing UI app). This is the single source of truth. `Yanflix.html` defines the look, stage flow, and API route names; this document defines the backend behavior, orchestration, and constraints. Where they conflict, THIS DOCUMENT WINS (specific overrides listed in Section 9).

---

## 0. Hard Constraints (These Override Everything)

1. **Cost = low and controlled. Groq is the primary cloud engine.**
   - **Groq** (`GROQ_API_KEY` in `.env.local`) handles transcription and all LLM calls. It runs entirely in the cloud — zero VRAM, zero RAM competition with local GPU jobs. Use it aggressively.
   - **Transcription:** Groq Whisper (`whisper-large-v3-turbo`) is the PRIMARY transcription engine. Groq's audio file limit is 25MB; `vocals.wav` is ~250MB. `transcribe.py` must therefore chunk the WAV into ~110-second segments (safely under 25MB at standard bitrates), POST each chunk to `https://api.groq.com/openai/v1/audio/transcriptions` with `model: whisper-large-v3-turbo`, and merge the timestamped JSON results applying per-chunk time offsets so timestamps are correct for the full episode. Fallback: local Faster-Whisper `medium` int8 if Groq fails or is unavailable.
   - **LLM cascade for ALL routes that call a language model (diarize, translate, direct):**
     1. `groq/llama-3.3-70b-versatile` (primary — fast, cloud, no GPU pressure)
     2. `gemini-2.0-flash` (free tier fallback)
     3. `gemini-2.0-flash-lite` (free tier fallback)
     4. `ollama/llama3.1:8b` (local last resort — ONLY if `jobs/gpu.lock` is not held; skip if GPU is busy)
   - **Groq LLM call format:** `https://api.groq.com/openai/v1/chat/completions`, OpenAI-compatible. Always `temperature: 0.1` for deterministic JSON output.
   - **MANDATORY for every LLM response before JSON.parse():** strip markdown fences: `text.replace(/```json/g,'').replace(/```/g,'').trim()`. Apply to Groq, Gemini, AND Ollama responses. Never let a raw LLM response hit JSON.parse() directly.
   - **Batching still required:** never call any LLM per-line. One request per episode for diarize, one for translate, one per song for lyrics. Groq's context window (128K for llama-3.3-70b) handles a full episode transcript easily.
   - **ElevenLabs:** 10,000 total credits. Used ONLY to build per-character emotional reference banks (7 short sentences × 8 emotions per character, once per character ever). NEVER for episode dialogue. Hard credit cap enforced in code (~2,000/show ceiling, configurable).
   - **All dialogue synthesis:** IndexTTS2, local. All song voice conversion: RVC, local.

2. **GPU = RTX 4050 Laptop, 6GB VRAM. ONE GPU job at a time, ever.**
   - File-based mutex: `jobs/gpu.lock` contains the active stage. Any API route starting a GPU job returns **HTTP 409** if the lock exists. **ROUTES NEVER WRITE THE LOCK — only Python workers write and delete it.** Workers delete the lock in a `finally` block regardless of success or failure.
   - Each Python worker: load model → process → `del model; torch.cuda.empty_cache()` → exit.
   - **Groq-based transcription does NOT use the GPU lock** — it's a cloud API call. Only the local Faster-Whisper fallback path in `transcribe.py` acquires the GPU lock.
   - IndexTTS2: load ONCE per run, batch all lines, resumable, exit. Still GPU-locked.
   - Demucs: GPU-locked, always local — no cloud equivalent.

3. **All state lives on disk.** `jobs/{ep_folder}/status_{stage}.json` per stage + `state_director.json` (Section 5) as the master per-line database, written through after every line. Crash anywhere → rerun resumes from the last completed line. No in-memory job maps (Next.js hot reload wipes them).

4. **Two clients, one backend, zero coupling between clients:**
   - **Next.js API (port 3000, native host)** is the only backend. It spawns Conda workers and owns the filesystem.
   - **Yanflix UI** (served by the same Next.js app) calls the API directly for manual, stage-by-stage operation.
   - **n8n (Docker, port 5678)** calls the SAME API via `http://host.docker.internal:3000` for unattended autopilot runs.
   - The UI observes autopilot progress purely by polling the SAME status files — n8n and the UI never communicate directly. The GPU lock makes dual control safe: if n8n owns a stage, UI buttons get a 409 and show a "busy — autopilot running" toast.

---

## 1. System Architecture

```
   ┌────────────────────────────┐        ┌──────────────────────────────┐
   │     Yanflix UI (React)     │        │   n8n Autopilot (Docker)     │
   │  8-stage Dub Studio,       │        │   Webhook-triggered chains,  │
   │  Library, Character Vault  │        │   Wait→Check→IF poll loops   │
   └──────────┬─────────────────┘        └──────────────┬───────────────┘
              │  fetch /api/*                           │  HTTP via
              │  + poll status files via /api/status    │  host.docker.internal:3000
              ▼                                         ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │              Next.js Native API (port 3000, Windows host)           │
   │   Spawns Conda workers · writes status JSON · enforces gpu.lock     │
   └──────────────────────────────┬──────────────────────────────────────┘
                                  │ sequential Conda child processes
                                  ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                 Local Hardware Layer (RTX 4050, 6GB)                 │
   │  env `sonitr`: Demucs, Faster-Whisper                                │
   │  env `dubbing`: IndexTTS2, RVC, Pydub, Rubberband, NISQA, ChromaDB   │
   │  External APIs (called from host, never from n8n): Gemini, ElevenLabs│
   └─────────────────────────────────────────────────────────────────────┘
```

### 1.1 Status & Progress Contract

- Every stage writes `jobs/{ep_folder}/status_{stage}.json`:
  `{ "stage": "...", "status": "processing|done|error", "progress": 0-100, "logs": [...], "error": null, "owner": "ui|n8n", "updated_at": "..." }`
- `GET /api/status?ep_folder=X` returns a merged view of all stage files + GPU lock holder. Both the UI's stage cards and `startLogPolling` and n8n's gatekeepers consume this one endpoint.
- "done" for filesystem-producing stages is double-checked against actual output files (e.g., isolate verifies `vocals.wav` exists).
- n8n IF gatekeepers have THREE branches: `done` → next stage; `error` → Stop And Error; else → loop to Wait. Include a poll counter; abort with error after 240 polls.

### 1.2 Cross-Workflow Data Passing (n8n)

n8n workflows cannot reference nodes in other workflows. Every webhook payload carries full context (`show_name`, `episode_id`, `ep_folder`, `track_mode`, `source_lang`, `raw_file_name`). Chaining uses Execute Workflow nodes or HTTP calls to the next webhook with the context JSON.

---

## 2. API Contract (UI route names are canonical — implement exactly these)

| Route | Method | Purpose | GPU |
|---|---|---|---|
| `/api/status` | GET | Merged stage statuses + logs + gpu.lock holder (UI poller + n8n gatekeepers) | – |
| `/api/characters` | GET | Character Vault listing (global_roster + shows) from disk + ChromaDB | – |
| `/api/ytdlp` | POST | Download source media via yt-dlp into `0_raw_videos/` | – |
| `/api/isolate` | POST | Demucs two-stem split (env `sonitr`); copies stems to stable paths | ✓ |
| `/api/transcribe` | POST | Faster-Whisper medium int8 on `vocals.wav` → timestamped JSON | ✓ |
| `/api/diarize` | POST | **Hybrid speaker ID** (Section 4.1): Gemini context-based assignment, returns proposed cast; pyannote optional refinement only if `use_pyannote: true` | (✓ if pyannote) |
| `/api/save_cast` | POST | Lock the human-reviewed speaker→character assignments into `state_director.json` | – |
| `/api/save_speaker_to_vault` | POST | Create character folder, copy NISQA-gated seed clips, register embedding in ChromaDB | – |
| `/api/clone_speakers` | POST | **ElevenLabs emotional bank builder** (Section 4.2): IVC clone + 7 `ref_{emotion}.wav` per new character; skips characters ChromaDB already knows | – |
| `/api/translate` | POST | Batched Gemini dual-translation: fills `text_standard` + `text_aave` for ALL lines in one call | – |
| `/api/direct` | POST | Batched Gemini emotion-tagging/natural-rewrite pass (fixed enum, Section 3); regenerable without touching translation | – |
| `/api/save_segments` | POST | Persist UI script-table edits (speaker, emotion, isSong flags, text) into `state_director.json` | – |
| `/api/actor` | POST | IndexTTS2 batch synthesis for `{ep_folder, track_mode}`; resumable; refuses (409) if other synth running | ✓ |
| `/api/regen_line` | POST | Re-synthesize ONE line (loads IndexTTS2, does one line, frees VRAM) | ✓ |
| `/api/regen_speaker` | POST | Re-synthesize all lines of one character | ✓ |
| `/api/voice_test` | POST | Synthesize a short test sentence with a chosen character+emotion ref | ✓ |
| `/api/fit_audio` | POST | Rubberband time-stretch batch for `{ep_folder, track_mode}` (CPU only) | – |
| `/api/dub_song` | POST | Song pipeline (Section 4.3), `{ep_folder, segment, path_mode}` | ✓ |
| `/api/render` | POST | FFmpeg final mux → `5_outputs/{ep_folder}_{track_mode}.mp4` (video stream copied, not re-encoded) | – |

POST trigger pattern (all GPU/long routes): validate input → check `gpu.lock` (409 if held) → write lock + `status: processing` → `spawn('conda', ['run','-n',ENV,'python',WORKER,...], {detached:true, stdio:'ignore'}).unref()` → return `{status:"processing"}` instantly. The worker writes progress/done/error to its status file and removes the lock in `finally`.

---

## 3. The Emotion Enum (FIXED shared contract)

```
neutral | cheerful | angry | sad | whisper | exhausted | excited | fearful
```

Used identically by: the Gemini director prompt (may ONLY output these strings; uncertain → `neutral`), the ElevenLabs bank filenames (`ref_{emotion}.wav`), the UI's per-line emotion dropdown, and the IndexTTS2 synthesis lookup (`characters/.../{char}/ref_{emotion}.wav`, fallback `ref_neutral.wav`, never crash on missing file).

ElevenLabs bank sentences: ~80–110 chars each, distinct natural sentences whose content matches the emotion. ≈700 credits/character. `global_roster/generic_male_01` and `generic_female_01` get banks too (built once, reused across all future projects for non-cloned characters).

---

## 4. Pipeline Stages (maps 1:1 to the UI's 8 Dub Studio stages)

UI stages: **1 Project → 2 Source Media → 3 Vocal Isolation → 4 Speakers → 5 Script → 6 Voices → 7 Mixer → 8 Export.** Backend behavior per stage:

### 4.0 Stages 1–3: Project / Source / Isolate
- Project/episode selection writes `ep_folder = {slug(show)}_{episode_id}` and initializes `jobs/{ep_folder}/`.
- Source: local file copy or `/api/ytdlp` into `workspace/0_raw_videos/`; FFmpeg extracts full episode audio to `1_inputs/`.
- Isolate: Demucs `--two-stems=vocals`. **Bug fix:** Demucs nests output as `2_isolated/{ep}/htdemucs/{basename}/vocals.wav` — the worker copies stems to stable paths `2_isolated/{ep}/vocals.wav` + `instrumental.wav`; everything downstream uses the stable paths. UI's stem preview players point at the stable paths.

### 4.1 Stage 4: Speakers — HYBRID identification (Gemini proposes, human disposes)
**FIXED decision:** primary speaker ID is Gemini-context-based, NOT pyannote. (Frequency-based diarization fragments one character into many "speakers" across emotional registers — previously produced 10 speakers for ~5 characters.)

Flow:
1. `/api/transcribe` must run first (Whisper transcript with timestamps).
2. `/api/diarize` sends the FULL transcript to Gemini (one batched request, part of the Director call — Section 6) which returns, for every line: a proposed character name (using narrative context across the whole episode), confidence, and song-segment identification (intro/outro with timestamps, `type:"singing"`).
3. The UI's existing Speakers cast board renders Gemini's proposals: detected speakers, sample-clip playback (per-line clips from `segment_lines`), assignment dropdowns, merge controls. The human corrects any mistakes.
4. `/api/save_cast` locks assignments into `state_director.json`. Stage 4 is not `done` until cast is saved.
- `use_pyannote: true` config flag keeps the UI's pyannote knobs functional as an OPTIONAL pre-pass whose clusters are given to Gemini as hints; default OFF.
- After cast lock, `segment_lines.py` (Pydub, CPU) slices `vocals.wav` into per-line clips `jobs/{ep}/line_clips/line_NNN.wav` (silence-boundary snapping) and extracts intro/outro song segments from BOTH stems into `jobs/{ep}/songs/`.

### 4.2 Stage 6: Voices — seeds, NISQA gate, ElevenLabs emotional banks
1. Per character: rank that character's line clips by **NISQA** MOS, keep top 3–5 clips ≥2s → `characters/shows/{show}/{char}/seeds/`.
2. Compute a speaker embedding (resemblyzer or similar) → upsert into **ChromaDB** (`voice_registry/chroma/`) with `{show, character, bank_complete}`. Future episodes query ChromaDB first; known characters with complete banks are skipped entirely (0 ElevenLabs credits from episode 2 onward).
3. `/api/clone_speakers`: for NEW characters only — ElevenLabs `/v1/voices/add` (instant clone from seeds) → 7 TTS calls (one per emotion sentence) → save `ref_{emotion}.wav` bank → write `profile.json` → optionally delete the cloud voice (the WAV bank is the asset; the cloud voice is disposable). Enforce credit cap; refuse and report if exceeded.
4. UI shows bank status per character; `/api/voice_test` lets the user audition any character+emotion through IndexTTS2 before committing to a full run.
- Config flag `use_direct_seeds` (default off): skip ElevenLabs, use original-language seeds as IndexTTS2 refs directly — free, but carries source accent.

### 4.3 Stage 5: Script + Songs

- `/api/translate` = ONE batched Gemini call producing `text_standard` AND `text_aave` for every line (prompt `02_dual_translation.md`). AAVE track uses natural phonetic spellings/fusions ("Whatchu gon do?", "lemme", "finna") — these steer IndexTTS2 toward connected natural cadence. Standard track uses conventional spelling.
- `/api/direct` = the emotion-tag / natural-rewrite pass (fixed enum). Independently regenerable.
- The UI script table edits (speaker, emotion, isSong, text per line) persist via `/api/save_segments`.

**Songs — two modes, selected per song entry via `song_source` field:**

#### Mode 1: Cache (series recurring songs — e.g. Smoking Behind the Supermarket)
Intro and outro songs are the same every episode. Dub them ONCE (Episode 1), save the result to the show's song vault, and every future episode pulls from the vault at render time — zero re-processing, zero GPU, zero Gemini credits.

- Song vault location: `characters/shows/{show}/songs/{segment}_{track_mode}.wav`
  - e.g. `characters/shows/smoking_behind_the_supermarket/songs/intro_standard.wav`
  - e.g. `characters/shows/smoking_behind_the_supermarket/songs/outro_aave.wav`
- When `song_source == "cache"`: the renderer reads directly from the vault path. `/api/dub-song` is never called. The WF3 song nodes are skipped entirely (the `IF: songs already done?` gate checks vault existence, not just episode status).
- After a song is dubbed for the first time, `build_voice_bank.py` (or a dedicated `/api/save-song-to-vault` route) copies the finished WAV to the vault and sets `song_source: "cache"` in `state_director.json` for all future episodes of that show.
- The UI should show a "From Vault ✓" badge on cached song rows in the Script stage instead of a dub button.

#### Mode 2: Generate (films and unique songs — e.g. 200 Pound Beauty)
Every song is unique to the scene and must be dubbed fresh. Full pipeline runs per song.

- `song_source == "generate"`: runs the full `/api/dub-song` pipeline (Path A or B).
- Reality constraints: IndexTTS2 is a speech model (cannot follow melody); RVC converts timbre only — its input supplies melody/rhythm.
- **Path A (default, $0):** Gemini singable translation (prompt `03_song_translation.md`, syllable-matched per line) → IndexTTS2 with artist seed clips → Rubberband fit → mix over instrumental stem. Result: speak-sung cover in the artist's timbre.
- **Path B (melodic, one human step):** human records a guide vocal → RVC (trained once on the isolated vocal stem, model cached in `characters/.../artist_x/rvc_model/`) → artist-timbre English vocal → mix.
- Output: `jobs/{ep}/songs/{segment}_dubbed.wav`. The song entry gets `cached_wav_path` + `status: song_complete`.
- For a film with many songs (200 Pound Beauty has ~10), each song entry in `state_director.json` is independent — they can be dubbed in any order and are all mode `generate`.

**`/api/dub-song` route behavior:**
- Check `song_source` field first. If `"cache"`, return `{"status":"done","source":"vault","path":"..."}` immediately without spawning any worker.
- If `"generate"`, spawn `dub_song.py` as normal.

**Song vault lookup at render time (`render_video.py`):**
For each song entry in `state_director.json`:
1. If `song_source == "cache"`: use `characters/shows/{show}/songs/{segment}_{track_mode}.wav`
2. If `song_source == "generate"` and `status == "song_complete"`: use `dubbed_wav` path
3. If neither: use original audio (untouched — no dub for this song yet)

### 4.4 Stages 6→7→8: Synthesis, Mixer, Export
- `/api/actor` (`synthesize_dub.py`, env `dubbing`): loads IndexTTS2 once; iterates lines where `type=="speech"` and `audio_synthesis_status[track_mode] != "done"`; per line selects `ref_{detected_emotion}.wav` of the line's character; writes `tts_audio/{track_mode}/raw_line_NNN.wav`; updates `state_director.json` after EVERY line (crash-safe); logs-and-continues past failed lines (retried next run); frees VRAM and exits. Standard and AAVE runs are serialized (409 if the other is processing).
- `/api/fit_audio` (`audio_fitter.py`, CPU): per line, α = (end−start)/duration, Rubberband pitch-preserving stretch, **clamp α to [0.7, 1.3]** with a logged warning when clamped → `fit_line_NNN.wav`. The UI Mixer's `stretchMin/stretchMax` settings override the clamp bounds.
- `/api/render` (`render_video.py`): FFmpeg — original video stream **copied** (no re-encode; NVENC only if re-encode is explicitly requested in Mixer settings) + audio bed = full-episode `instrumental.wav` at `bgVol` with every `fit_line_NNN.wav` overlaid at its `start` via adelay/amix at `dubVol`, chunked ~50 lines per filtergraph then concatenated (keeps filtergraphs manageable for 300+ lines). Songs from 4.3 placed at their timestamps. Output: `workspace/5_outputs/{ep_folder}_{track_mode}.mp4` — per-episode, per-track names; Standard and AAVE coexist. The Library reads `5_outputs/`.

---

## 5. Master Data Schema: `jobs/{ep_folder}/state_director.json`

```json
{
  "ep_folder": "smoking_supermarket_s01e01",
  "show_name": "smoking_behind_the_supermarket",
  "source_lang": "ja",
  "cast_locked": true,
  "characters": {
    "sasaki": { "bank_dir": "characters/shows/smoking_behind_the_supermarket/sasaki", "bank_complete": true, "chroma_id": "..." },
    "yamada": { "bank_dir": "characters/shows/smoking_behind_the_supermarket/yamada", "bank_complete": true, "chroma_id": "..." }
  },
  "songs": [
    {
      "segment": "intro",
      "artist": "artist_zutomayo",
      "start": 120.45, "end": 185.0,
      "song_source": "generate",
      "lyrics_source": "...",
      "lyrics_english": "...",
      "path_mode": "A",
      "dubbed_wav": "",
      "vault_wav": "",
      "status": "pending"
    }
  ],
  "lines": [
    {
      "line_index": 0,
      "start": 1.24, "end": 4.58,
      "character": "sasaki",
      "speaker_confidence": 0.93,
      "type": "speech",
      "detected_emotion": "exhausted",
      "source_text": "はぁ、今日も残業か...",
      "text_standard": "Sigh, overtime again today...",
      "text_aave": "Man... straight overtime again today.",
      "clip_path": "jobs/smoking_supermarket_s01e01/line_clips/line_000.wav",
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

All workers preserve unknown fields. Per-line per-track statuses: `pending|done|error`.

`synthesis_quality` values per track: `pending` | `passed` | `flagged` | `error`
- Written by `audio_fitter.py` after the stretch step on every fitted line.
- `flagged` = MOS score below threshold (default 3.2 on a 1–5 scale). Line needs regeneration.
- `passed` = MOS above threshold. Safe to render.
- `mos_score` stores the raw float from NISQA (or heuristic fallback).

**QC flow:**
1. `audio_fitter.py` fits every line and immediately scores it. No separate QC step.
2. The fit status file `status_fit_{track}.json` includes `result.qc_flagged` (count) and `result.flagged_lines` (array of line_index integers).
3. **UI — Script stage:** reads `flagged_lines` from the fit status file and highlights those rows in red with a ⚠ badge and MOS score displayed. The existing per-line Regenerate button re-synthesizes and re-fits that line, clearing the flag if the new score passes.
4. **n8n WF3 — render gate:** after the fit poll resolves `done`, add an IF node before `POST: render video`:
   - Name: `IF: QC clear to render?`
   - Condition: `{{ $json.result.qc_flagged }}` Number equals `0`
   - TRUE → proceed to render
   - FALSE → route to a node named `Stop: QC review needed` (Stop And Error, message: "Flagged lines need review before render — check the Script stage in the UI")
   - This forces a human review pass on any flagged lines before committing to a 45-minute FFmpeg render.
   - If you want to render anyway despite flags (e.g. for a draft preview), add a `--skip_qc_gate` flag to the webhook payload and bypass this IF node.

---

## 6. Gemini Free-Tier Strategy (Batching Is Mandatory)

Free tier ≈ 15 RPM / 1,500 req/day; a 24-min episode has 200–400 lines. Per-line calls are FORBIDDEN. **≤ 4 requests per episode:**

1. **Director call** (`01_script_director.md`): full Whisper transcript in → JSON out with per-line speaker proposal + confidence, emotion (fixed enum), cleaned boundaries, song segment detection. `responseMimeType: application/json` + response schema.
2. **Dual translation call** (`02_dual_translation.md`): all lines in → `text_standard` + `text_aave` for all lines out. Shared by both dub tracks.
3–4. **Song translation calls** (`03_song_translation.md`): one per song, singable syllable-matched English lyrics.

Validation: assert returned line count == input line count; schema-validate; on malformed JSON re-prompt ONCE with the validation errors appended; never silently accept partial arrays. `/api/direct` re-runs are a repeat of call type 1's emotion fields only (still one batched call).

---

## 7. n8n Workflow Specifications (Explicit — Build Exactly This)

There are **3 n8n workflows total**. Their exact names, every node name, every node type, every field value, and every connection are specified below. Do not rename nodes. Do not invent extra nodes.

**DELETE these old workflows if they exist:**
- "Yanflix ◆ WF3: Voice Generation & Audio Fitting" — DELETED, replaced by WF3 below
- "Yanflix ◆ WF4: Cinematic Video Compositing" — DELETED, merged into WF3 below

All HTTP Request nodes use Base URL: `http://host.docker.internal:3000`

---

### WORKFLOW: "Yanflix ◆ WF3: Dub Pipeline (Standard + AAVE)"

This single workflow handles translation, song dubbing, synthesis, fitting, and rendering for BOTH the Standard and AAVE tracks. It is NOT a line-by-line loop. It uses SplitInBatches to run the track block twice sequentially.

Total nodes: 32 (plus 5 Stop And Error nodes).

#### SHARED PHASE — runs once for the episode

**Node 1**
- Name: `Webhook: run-dub`
- Type: Webhook
- HTTP Method: POST
- Path: `run-dub`
- Response Mode: Immediately
- Expected payload: `{ "ep_folder": "smoking_supermarket_s01e01", "track_modes": ["standard","aave"] }`

**Node 2**
- Name: `GET: translate status`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query Parameter: `ep_folder` = `{{ $json.body.ep_folder }}`

**Node 3**
- Name: `IF: translate already done?`
- Type: IF
- Condition: `{{ $json.status_translate }}` String equals `done`
- TRUE output → connect to Node 7
- FALSE output → connect to Node 4

**Node 4**
- Name: `POST: translate`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/translate`
- Body Type: JSON
- Body: `{ "ep_folder": "{{ $node["Webhook: run-dub"].json.body.ep_folder }}" }`

**Node 5**
- Name: `Wait: translate buffer`
- Type: Wait
- Resume: After time interval
- Amount: 8 / Unit: Seconds

**Node 6**
- Name: `GET: translate poll`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query Parameter: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 6a**
- Name: `IF: translate done?`
- Type: IF
- Condition A: `{{ $json.status_translate }}` equals `done` → TRUE → Node 7
- Condition B: `{{ $json.status_translate }}` equals `error` → connect to Stop And Error node named `Stop: translate error`
- else FALSE → back to Node 5
- Poll abort: use a Code node counter after 20 loops → `Stop: translate error`

**Node 7**
- Name: `GET: song status`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query Parameter: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 8**
- Name: `IF: songs already done?`
- Type: IF
- Condition: `{{ $json.status_song_intro }}` String equals `done`
- TRUE output → connect to Node 14
- FALSE output → connect to Node 9
- **Note on cached songs:** when `song_source == "cache"` for a song entry, `/api/dub-song` returns `{status:"done"}` instantly without spawning any worker (the vault file already exists). The poll loop resolves in one cycle. This means Episode 2+ of a series passes through Nodes 9–13 in under a second — no special n8n logic needed.

**Node 9**
- Name: `POST: dub intro song`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/dub-song`
- Body: `{ "ep_folder": "{{ $node["Webhook: run-dub"].json.body.ep_folder }}", "segment": "intro", "path_mode": "A" }`

**Node 10**
- Name: `Wait: intro song buffer`
- Type: Wait / Amount: 30 / Unit: Seconds

**Node 11**
- Name: `GET: intro song poll`
- Type: HTTP Request GET `/api/status`
- Query: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 11a**
- Name: `IF: intro song done?`
- Type: IF
- `status_song_intro` equals `done` → Node 12
- `status_song_intro` equals `error` → `Stop: song error`
- else → back to Node 10
- Poll abort after 120 loops

**Node 12**
- Name: `POST: dub outro song`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/dub-song`
- Body: `{ "ep_folder": "{{ $node["Webhook: run-dub"].json.body.ep_folder }}", "segment": "outro", "path_mode": "A" }`

**Node 13**
- Name: `Wait: outro song buffer`
- Type: Wait / Amount: 30 / Unit: Seconds

**Node 13a**
- Name: `GET: outro song poll`
- Type: HTTP Request GET `/api/status`

**Node 13b**
- Name: `IF: outro song done?`
- `status_song_outro` equals `done` → Node 14
- `status_song_outro` equals `error` → `Stop: song error`
- else → back to Node 13
- Poll abort after 120 loops

---

#### TRACK PHASE — loops twice (standard then aave)

**Node 14**
- Name: `Set: track list`
- Type: Set
- Field: `track_modes` = `{{ $node["Webhook: run-dub"].json.body.track_modes }}`
- (value is the array ["standard","aave"] from the webhook payload)

**Node 15**
- Name: `SplitInBatches: per track`
- Type: SplitInBatches
- Batch Size: 1
- Each iteration carries the current track string as `{{ $json }}`
- "loop" output → Node 16 (start of per-track block)
- "done" output (all batches finished) → Node 32

**Node 16**
- Name: `GET: synth status`
- Type: HTTP Request GET `/api/status`
- Query: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 17**
- Name: `IF: synth already done?`
- Type: IF
- Condition: `{{ $json["status_synth_" + $node["SplitInBatches: per track"].json] }}` equals `done`
- TRUE → Node 21
- FALSE → Node 18

**Node 18**
- Name: `POST: synthesize dub`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/actor`
- Body: `{ "ep_folder": "{{ $node["Webhook: run-dub"].json.body.ep_folder }}", "track_mode": "{{ $node["SplitInBatches: per track"].json }}" }`

**Node 19**
- Name: `Wait: synth buffer`
- Type: Wait / Amount: 45 / Unit: Seconds

**Node 20**
- Name: `GET: synth poll`
- Type: HTTP Request GET `/api/status`
- Query: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 20a**
- Name: `IF: synth done?`
- Type: IF
- `status_synth_{track}` equals `done` → Node 21
- `status_synth_{track}` equals `error` → `Stop: synth error`
- HTTP status 409 (gpu busy) → `Wait: GPU busy retry` (Wait 60s) → back to Node 18
- else → back to Node 19
- Poll abort after 200 loops (IndexTTS2 on 300+ lines can take 2+ hours)

**Node 21**
- Name: `GET: fit status`
- Type: HTTP Request GET `/api/status`
- Query: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 22**
- Name: `IF: fit already done?`
- Condition: `{{ $json["status_fit_" + $node["SplitInBatches: per track"].json] }}` equals `done`
- TRUE → Node 26
- FALSE → Node 23

**Node 23**
- Name: `POST: fit audio`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/fit-audio`
- Body: `{ "ep_folder": "{{ $node["Webhook: run-dub"].json.body.ep_folder }}", "track_mode": "{{ $node["SplitInBatches: per track"].json }}" }`

**Node 24**
- Name: `Wait: fit buffer`
- Type: Wait / Amount: 10 / Unit: Seconds

**Node 25**
- Name: `GET: fit poll`
- Type: HTTP Request GET `/api/status`

**Node 25a**
- Name: `IF: fit done?`
- `status_fit_{track}` equals `done` → Node 26
- `status_fit_{track}` equals `error` → `Stop: fit error`
- else → back to Node 24
- Poll abort after 60 loops

**Node 26**
- Name: `GET: render status`
- Type: HTTP Request GET `/api/status`
- Query: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 27**
- Name: `IF: render already done?`
- Condition: `{{ $json["status_render_" + $node["SplitInBatches: per track"].json] }}` equals `done`
- TRUE → Node 15 (back to SplitInBatches for next track)
- FALSE → Node 27a

**Node 27a**
- Name: `GET: fit result for QC`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query Parameter: `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`

**Node 27b**
- Name: `IF: QC clear to render?`
- Type: IF
- Condition: `{{ $json["fit_result_" + $node["SplitInBatches: per track"].json + "_qc_flagged"] ?? 0 }}` Number equals `0`
- TRUE → Node 28
- FALSE → Check `skip_qc_gate` flag:
  - If `{{ $node["Webhook: run-dub"].json.body.skip_qc_gate }}` is `true` → Node 28 (draft render, bypasses review)
  - Else → `Stop: QC review needed` (type: Stop And Error, message: "Flagged lines found — open the Script stage in the UI, regenerate red-highlighted lines, then re-trigger. Add skip_qc_gate:true to the payload to force a draft render anyway.")

**Note for `/api/status` route:** expose `fit_result_{track}_qc_flagged` as a top-level key by reading `result.qc_flagged` from `status_fit_{track}.json`. Example: `fit_result_standard_qc_flagged: 3`.

**Node 28**
- Name: `POST: render video`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/render`
- Body: `{ "ep_folder": "{{ $node["Webhook: run-dub"].json.body.ep_folder }}", "track_mode": "{{ $node["SplitInBatches: per track"].json }}" }`

**Node 29**
- Name: `Wait: render buffer`
- Type: Wait / Amount: 15 / Unit: Seconds

**Node 30**
- Name: `GET: render poll`
- Type: HTTP Request GET `/api/status`

**Node 30a**
- Name: `IF: render done?`
- `status_render_{track}` equals `done` → back to Node 15 (SplitInBatches next iteration)
- `status_render_{track}` equals `error` → `Stop: render error`
- else → back to Node 29
- Poll abort after 60 loops

**Node 32** (terminal — reached from SplitInBatches "done" output)
- Name: `Set: pipeline complete`
- Type: Set
- Fields:
  - `status` = `complete`
  - `ep_folder` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}`
  - `standard_output` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}_standard.mp4`
  - `aave_output` = `{{ $node["Webhook: run-dub"].json.body.ep_folder }}_aave.mp4`
- This is the terminal node. No SSE node. The UI polls `/api/status` and sees completion from the render status files automatically.

---

### WORKFLOW: "Yanflix ◆ WF1: Character Vault & Voice Bank"

Keep the existing live implementation. Confirm node names do NOT include any of: "Trigger Compositing Link", "Compile Master Video", "UI SSE Notification", "Line Iterator Loop", "Load Script Elements". If any of those names appear in WF1, rename them — they are old WF3/WF4 artifacts.

---

### WORKFLOW: "Yanflix ◆ WF2: Gemini Script Director"

Keep the existing live implementation.

---

### Status field naming contract — `/api/status` response keys

The IF nodes in WF3 reference these exact field names. The `/api/status` GET route MUST read these exact `status_{stage}.json` filenames and expose them under these exact keys:

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
  "gpu_lock_holder":        "stage:ep_folder string or null"
}
```

Missing status file = `"offline"`. Python workers write files named exactly: `status_synth_standard.json`, `status_fit_aave.json`, `status_render_standard.json` etc. — the suffix IS the track_mode string. Verify filenames match before wiring the IF nodes.

---

## 8. File System Layout

```text
yanflix-dubbing-studio/
├── .env.local                          # GEMINI_API_KEY, ELEVENLABS_API_KEY
├── app/
│   ├── page.tsx                        # Yanflix UI (ported from Yanflix.html into Next.js)
│   ├── globals.css                     # crimson/gold cinematic theme from Yanflix.html
│   └── api/                            # all routes from Section 2 table
├── characters/
│   ├── global_roster/{generic_male_01,generic_female_01}/   # ref_{emotion}.wav ×8 + profile.json
│   └── shows/{show}/
│       ├── songs/                          # Song vault — dubbed once per show, reused every episode
│       │   ├── intro_standard.wav          # e.g. Zutomayo intro, Standard dub
│       │   ├── intro_aave.wav
│       │   ├── outro_standard.wav          # e.g. Imase outro, Standard dub
│       │   └── outro_aave.wav
│       └── {character}/                    # seeds/ + ref_{emotion}.wav ×8 + profile.json (+ rvc_model/ for artists)
├── voice_registry/chroma/              # ChromaDB persistent store
├── jobs/{ep_folder}/
│   ├── gpu.lock (transient, repo-root jobs/ level)
│   ├── status_{stage}.json
│   ├── state_director.json
│   ├── line_clips/line_NNN.wav
│   ├── songs/{intro,outro}_{vocals,instrumental,dubbed}.wav
│   └── tts_audio/{standard,aave}/{raw,fit}_line_NNN.wav
├── prompts/
│   ├── 01_script_director.md
│   ├── 02_dual_translation.md
│   └── 03_song_translation.md
├── python_backend/
│   ├── isolate.py  transcribe.py  segment_lines.py  harvest_voices.py
│   ├── build_voice_bank.py  synthesize_dub.py  audio_fitter.py
│   ├── dub_song.py  render_video.py
└── workspace/
    ├── 0_raw_videos/  1_inputs/  2_isolated/  3_transcripts/  5_outputs/
```

---

## 9. Required Changes to Yanflix.html (UI is otherwise canonical)

1. **Settings → "Ollama (Director LLM)" panel becomes "Gemini (Director LLM)":** API key field (masked, reveal toggle like the HF token row), model dropdown (`gemini-2.0-flash` default, `gemini-1.5-flash` fallback). Remove Ollama URL/model fields and all "Ollama" toast copy ("Gemini translating dialogue…", "Gemini adding emotion tags…"). Remove `ollamaChunk` (batching replaces chunking). Drop Ollama from the system-status checklist; add Gemini (key present + test call) and ElevenLabs (key + remaining credits).
2. **Whisper default:** placeholder/help text recommends `medium` (int8) for 6GB VRAM, not large-v3.
3. **Speakers stage:** keep the cast board UI; it now renders Gemini's proposed assignments (with confidence badges) instead of raw pyannote clusters. pyannote knobs remain but behind a "use pyannote pre-pass" toggle, default off. HF token stays optional for that toggle.
4. **Voices stage additions:** per-character emotional-bank status grid (8 emotions, built/missing), "Build Bank (ElevenLabs)" button with credit estimate + confirmation, "already in vault — 0 credits" badge for ChromaDB-known characters.
5. **Script stage addition:** the translation produces both tracks at once; add a Standard/AAVE toggle to preview either text column in the script table.
6. **Export stage addition:** track selector (Standard / AAVE / both) driving `/api/actor`+`fit`+`render` per track; outputs named `{ep_folder}_{track_mode}.mp4`.
7. **Autopilot visibility:** stage cards driven by `/api/status` polling regardless of who triggered the stage; show an "AUTOPILOT" pill when `owner=="n8n"`; UI action buttons disabled-with-tooltip while gpu.lock is held by another owner; a "Waiting for your cast review" banner when autopilot is paused at the cast-lock gate.
8. **System status footer:** `ws-dot` reflects `/api/status` reachability (already wired); add GPU lock holder display.
9. The fake `sysCheck = {everything: true}` object becomes a real `/api/status` system check (ffmpeg, demucs, whisper, indextts, gemini key, elevenlabs key, chromadb).

---

## 10. Confirmed Bugs — Fix These Before Anything Else

These three bugs are confirmed from live testing. They block the entire pipeline. Fix them first.

---

### Bug 1: WF1 has the wrong trigger node — Autopilot button does nothing

**Symptom:** Pressing Autopilot in the UI fires a POST request that no workflow is listening for. WF1's current trigger is "When clicking 'Execute workflow'" — a manual n8n UI button, not a webhook. Nothing connects to it from the outside.

**Fix — WF1 trigger node:**
- Delete the "When clicking 'Execute workflow'" node from WF1 entirely.
- Replace it with a **Webhook** node:
  - Name: `Webhook: harvest-characters`
  - Type: Webhook
  - HTTP Method: POST
  - Path: `harvest-characters`
  - Response Mode: Immediately
- **Rename WF1** to exactly: `Yanflix ◆ WF1: Character Vault & Voice Bank`
- **Activate WF1** (toggle to Active in the n8n top bar). Inactive workflows do not listen on their production webhook URL even if the node is correct.

**Fix — Autopilot button in Next.js UI:**

The Autopilot button must POST to the **production** webhook URL, not the test URL. These are different:
- Test URL (only works while sitting in the n8n editor): `http://localhost:5678/webhook-test/harvest-characters`
- Production URL (always active when workflow is activated): `http://localhost:5678/webhook/harvest-characters`

Find the Autopilot onClick handler in `app/page.tsx` (or wherever the button is wired). The fetch call must point to the production URL:

```typescript
// WRONG — test URL, only works in n8n editor
await fetch('http://localhost:5678/webhook-test/harvest-characters', { method: 'POST', ... })

// CORRECT — production URL, works when workflow is Active
await fetch('http://localhost:5678/webhook/harvest-characters', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    ep_folder: currentEpFolder,
    show_name: currentShowName,
    episode_id: currentEpisodeId,
    source_lang: 'ja',
    raw_file_name: currentRawFileName
  })
})
```

Store the n8n base URL (`http://localhost:5678`) in `.env.local` as `N8N_BASE_URL` so it's configurable. Never hardcode `webhook-test` anywhere in production code.

---

### Bug 2: WF2 is named wrong and has no webhook trigger

**Symptom:** WF2 is currently named "Yanflix WF2: Script Translation Director" (missing the ◆) and its trigger may not be a proper webhook either.

**Fix:**
- Rename to exactly: `Yanflix ◆ WF2: Gemini Script Director`
- Confirm its trigger node is a **Webhook** node with path `script-director`, method POST.
- Confirm the workflow is **Active**.
- The UI's "Run Script Director" button (or equivalent) must POST to `http://localhost:5678/webhook/script-director`.

---

### Bug 3: Demucs fails on filenames with spaces and square brackets

**Symptom:** The source file is named `Smoking Behind the Supermarket with You Episode 1 [smoking-behind-the-supermarket-with-you-episode-1].mp4`. Square brackets `[]` are glob metacharacters in bash. When `isolate.py` builds a shell command string containing this path — even with quotes — the brackets cause the shell to attempt glob expansion, producing a "no such file" error or silently processing the wrong file. Audio is never separated.

**Fix — `python_backend/isolate.py`:**

Never use `subprocess.run(cmd, shell=True)` or `exec(cmdString)` with user-supplied file paths. Use an **args list** with `shell=False` (the default):

```python
# WRONG — shell=True interprets brackets as globs, breaks on special chars
import subprocess
cmd = f'conda run -n sonitr demucs --two-stems=vocals "{video_path}" -o "{output_dir}"'
subprocess.run(cmd, shell=True)

# CORRECT — args list, shell never sees the path, all special chars safe
import subprocess
subprocess.run([
    "conda", "run", "-n", "sonitr",
    "demucs", "--two-stems=vocals",
    str(video_path),
    "-o", str(output_dir)
], check=True, shell=False)
```

This fix applies to **every** Python worker that spawns a subprocess with a file path argument:
- `isolate.py` — Demucs command
- `transcribe.py` — Faster-Whisper command
- `segment_lines.py` — any FFmpeg calls
- `audio_fitter.py` — Rubberband command
- `synthesize_dub.py` — IndexTTS2 command
- `render_video.py` — FFmpeg mux command

**Rule: no Python file in `python_backend/` may use `shell=True` with any path that comes from user input or the filesystem.** Always pass paths as list elements.

**Also fix in `app/api/isolate/route.ts`:**

The Next.js route spawns the Conda worker. Same rule applies — use the array form of `spawn`, never template-string shell commands:

```typescript
// WRONG
const cmd = `conda run -n sonitr python ./python_backend/isolate.py --video "${videoPath}"`;
exec(cmd);  // brackets in videoPath will break this

// CORRECT
const worker = spawn('conda', [
  'run', '-n', 'sonitr',
  'python', './python_backend/isolate.py',
  '--video', videoPath,      // passed as separate arg, never shell-interpreted
  '--ep', epFolder,
  '--output_dir', outputDir,
], { detached: true, stdio: 'ignore', shell: false });
worker.unref();
```

This same array-form fix applies to every `spawn` or `exec` call in every route file under `app/api/`.

---

## 11. Build & Verification Checklist

* [ ] All Section 2 routes implemented with the file-based status pattern; no in-memory job maps anywhere.
* [ ] gpu.lock honored everywhere: trigger a UI action during an n8n run → 409 + "autopilot busy" toast. Worker crash still releases the lock (finally block tested by kill -9).
* [ ] `nvidia-smi` never shows two model processes simultaneously across a full autopilot run.
* [ ] Kill Next.js mid-job, restart → polling still resolves (state survives on disk).
* [ ] Kill `synthesize_dub.py` at line ~180/300, rerun → resumes at 180, completed lines untouched.
* [ ] Gemini ≤4 calls per episode; line-count assertion + schema validation + single re-prompt on malformed JSON + 429 backoff all tested.
* [ ] Episode 2 of the same show: ChromaDB recognizes both leads → `/api/clone_speakers` spends 0 credits.
* [ ] NISQA gate rejects a deliberately noisy seed clip.
* [ ] Demucs nested path handled; stable stem paths exist; isolate-status verifies files on disk.
* [ ] Rubberband clamp [0.7, 1.3] logs warnings; Mixer settings override bounds.
* [ ] Cast-review pause works end-to-end: autopilot waits, human edits + saves cast in UI, autopilot resumes within one poll cycle.
* [ ] Song Path A produces `intro_dubbed.wav`; renderer places it correctly; absent dubbed song → original audio passes through.
* [ ] Both `{ep}_standard.mp4` and `{ep}_aave.mp4` render, coexist, and appear in the Library.
* [ ] n8n IF nodes all have done/error/loop branches + 240-poll abort; an injected worker error stops the run instead of looping forever.
* [ ] **Bug 1 verified:** Autopilot button POSTs to production webhook URL; WF1 is Active; n8n receives the payload and execution starts.
* [ ] **Bug 2 verified:** WF2 renamed and Active; script-director webhook responds to POST.
* [ ] **Bug 3 verified:** Rename source file to remove brackets (e.g. `Smoking_Behind_the_Supermarket_S01E01.mp4`), re-run isolate, confirm `vocals.wav` and `no_vocals.wav` appear at stable paths. Confirm no `shell=True` exists anywhere in `python_backend/` or `app/api/`.
* [ ] **Bug 4 verified:** Source MP4 is read from and stored in `workspace/0_raw_videos/` only. `workspace/1_inputs/` contains only extracted audio (WAV/MP3) — never an MP4. Fix: the Source Media stage must copy/move the file to `0_raw_videos/`, then FFmpeg extracts the audio track to `1_inputs/{ep_folder}_audio.wav`. The isolate route reads the video from `0_raw_videos/`, not `1_inputs/`. If the UI's file picker or ytdlp route is currently writing the MP4 to `1_inputs/`, that path is wrong — change it to `0_raw_videos/`.
* [ ] **QC verified:** After fit runs, `status_fit_standard.json` contains `result.qc_flagged` count and `result.flagged_lines` array. Open the Script stage in the UI — flagged lines are highlighted red with MOS score. Regenerate one flagged line, confirm it re-runs fit+score and clears the flag if score improves.
* [ ] **Song vault verified (series):** After Episode 1 dubs the intro song, `characters/shows/{show}/songs/intro_standard.wav` exists. Trigger Episode 2 — confirm song nodes resolve instantly from vault, no `dub_song.py` spawned, no GPU time used.
* [ ] **Song vault verified (film):** 200 Pound Beauty episode with `song_source: "generate"` on all songs — confirm each song runs full pipeline independently. Confirm vault folder is NOT written (films don't cache to vault).

---

## 12. Groq Integration (Added v3.1)

### 12.1 Why Groq

The RTX 4050 has 6GB VRAM and is shared between Demucs, Faster-Whisper, IndexTTS2, and RVC. Running LLM inference locally (even llama3.1:8b via Ollama) competes for that headroom and causes freezes. Groq runs on Groq's LPU hardware — zero local RAM/VRAM impact, responses in seconds.

### 12.2 Environment Variables (`.env.local`)

```
GROQ_API_KEY=<your key>
GEMINI_API_KEY=<your key>
ELEVENLABS_API_KEY=<your key>
N8N_BASE_URL=http://localhost:5678
CHROMA_HOST=localhost
CHROMA_PORT=8000
CHARACTERS_ROOT=./characters
WORKSPACE_ROOT=./workspace
```

### 12.3 Transcription: Chunked Groq Whisper

`python_backend/transcribe.py` implementation:

```python
# Pseudocode — implement exactly this logic
GROQ_AUDIO_LIMIT_MB = 23  # leave headroom under 25MB hard limit
CHUNK_SECONDS = 110       # ~110s of audio at 256kbps ≈ 21MB — safe

def transcribe_with_groq(vocals_path, source_lang):
    audio = AudioSegment.from_wav(vocals_path)
    duration_s = len(audio) / 1000
    chunks = []
    offset = 0.0
    
    for start_ms in range(0, len(audio), CHUNK_SECONDS * 1000):
        chunk = audio[start_ms : start_ms + CHUNK_SECONDS * 1000]
        # Export chunk to temp WAV
        tmp = f"/tmp/chunk_{start_ms}.wav"
        chunk.export(tmp, format="wav")
        
        # POST to Groq
        with open(tmp, "rb") as f:
            response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": (f"chunk.wav", f, "audio/wav")},
                data={"model": "whisper-large-v3-turbo", "language": source_lang,
                      "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
            )
        
        result = response.json()
        # Apply time offset to all segment timestamps
        for seg in result.get("segments", []):
            seg["start"] += offset
            seg["end"]   += offset
            chunks.append(seg)
        
        offset += (len(chunk) / 1000)  # advance by chunk duration in seconds
        os.remove(tmp)
    
    return chunks  # merged, time-corrected segments for full episode
```

Fallback: if Groq returns an error or `GROQ_API_KEY` is missing, fall through to local Faster-Whisper `medium` int8 with GPU lock.

### 12.4 LLM Routes: Groq First

Every route file that calls an LLM (`app/api/diarize/route.ts`, `app/api/translate/route.ts`, `app/api/direct/route.ts`) must implement this cascade:

```typescript
async function callLLM(prompt: string, systemPrompt: string): Promise<string> {
  // 1. Try Groq
  try {
    const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: { "Authorization": `Bearer ${process.env.GROQ_API_KEY}`,
                 "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile",
        temperature: 0.1,
        messages: [{ role: "system", content: systemPrompt },
                   { role: "user",   content: prompt }]
      })
    });
    if (res.ok) {
      const data = await res.json();
      return stripMarkdown(data.choices[0].message.content);
    }
  } catch(e) { /* fall through */ }

  // 2. Try Gemini 2.0 Flash
  try { /* gemini-2.0-flash call */ } catch(e) {}

  // 3. Try Gemini 2.0 Flash Lite
  try { /* gemini-2.0-flash-lite call */ } catch(e) {}

  // 4. Ollama last resort — only if GPU is not busy
  const lockPath = "./jobs/gpu.lock";
  if (!fs.existsSync(lockPath)) {
    const res = await fetch("http://localhost:11434/api/generate", {
      method: "POST",
      body: JSON.stringify({ model: "llama3.1:8b", prompt, stream: false, format: "json" })
    });
    const data = await res.json();
    return stripMarkdown(data.response);
  }

  throw new Error("All LLM providers failed or GPU busy");
}

// Apply to EVERY response before JSON.parse()
function stripMarkdown(text: string): string {
  return text.replace(/```json/g, '').replace(/```/g, '').trim();
}
```

### 12.5 What Changed vs Prior Spec

| Stage | Before | After |
|---|---|---|
| Transcription | Local Faster-Whisper (GPU, ~3 min) | Groq Whisper chunked (cloud, ~30s) |
| Speaker ID / diarize | Gemini 2.0 Flash | Groq llama-3.3-70b → Gemini fallback |
| Translation | Gemini 2.0 Flash | Groq llama-3.3-70b → Gemini fallback |
| GPU lock for transcribe | Always required | Only for local fallback path |
| Stem isolation (Demucs) | Local GPU | Local GPU (unchanged — no cloud option) |
| Synthesis (IndexTTS2) | Local GPU | Local GPU (unchanged) |
| Song conversion (RVC) | Local GPU | Local GPU (unchanged) |

### 12.6 Confirmed Bugs Fixed (from live testing)

- **Routes were writing `gpu.lock` before spawning workers** — workers saw the lock as held by themselves and exited immediately with code 2. Fix: routes only CHECK the lock (409 if held); only workers WRITE and DELETE it.
- **Stale GPU lock after process crash** — lock persists forever if worker dies without running `finally`. Fix: always `Remove-Item jobs/gpu.lock` before re-triggering a failed stage. The API should expose a `DELETE /api/gpu-lock` endpoint for the UI to call when clearing a crashed stage.
- **n8n expressions not evaluating in `jsonBody`** — `specifyBody: "json"` does not evaluate `={{ }}` expressions inside string values. Fix: use `contentType: "json"` + `bodyParameters` with individual named fields. Each field's `value` evaluates its own expression.
- **n8n expression corruption with single quotes in Python f-strings** — `$node['Name']` inside a Python f-string drops the `$node[` prefix. Fix: always build n8n expression strings with concatenation, never f-string interpolation around the `$node[` part.
- **`specifyBody` vs `sendBody`** — `sendBody: true` must be explicitly set or n8n defaults to `false` and sends no body regardless of other settings.