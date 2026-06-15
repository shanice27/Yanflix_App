# Yanflix WF1 Execution Log

---

## Execution 1 — 2026-06-13

### Trigger
```
curl -X POST http://localhost:5678/webhook/harvest-characters \
  -d '{"show_name":"smoking_supermarket","episode_id":"s01e01","ep_folder":"smoking_supermarket_s01e01","raw_file_name":"Smoking Behind the Supermarket with You Episode 1.mp4","source_lang":"ja"}'
```

---

### Error 1 — Body content type: expressions not evaluated

**Node:** Trigger Isolate  
**n8n error:** `400 — File not found: ={{ './workspace/0_raw_videos/' + $node['Webhook: harvest-characters'].json.body.raw_file_name }}`

**Root cause:** Using `specifyBody: "json"` + `jsonBody` in the HTTP Request node does NOT evaluate `={{ }}` expressions inside string values — n8n sends them as literal text. The route received the raw expression string instead of the resolved filename.

**Fix:** Switched all 5 WF1 POST nodes (Trigger Isolate, Trigger Transcribe, Trigger Segment Lines, Harvest Seeds, Clone Speakers) from `specifyBody: "json"` to `contentType: "json"` + `bodyParameters`. This combination evaluates each field's expression AND sends `Content-Type: application/json`.

---

### Error 2 — Stale webhook node name references

**Nodes affected:** Trigger Transcribe, Trigger Segment Lines, Harvest Seeds, Clone Speakers  
**Root cause:** These nodes still referenced `$node["Webhook"]` (the old deleted manual trigger). The webhook node was renamed to `Webhook: harvest-characters` in a previous session but these nodes weren't updated. Would have caused all stages after isolate to send `undefined` for `ep_folder`.

**Fix:** All 4 nodes updated to reference `$node['Webhook: harvest-characters']`.

---

### Error 3 — Trigger Isolate expression corruption (earlier patch)

**Root cause:** When the `$node['...']` expressions were patched via Python f-string with single quotes inside the value, the `$node[` prefix was dropped — n8n received `['Webhook: harvest-characters'].json.body...` without the `$node` prefix, which evaluates to undefined.

**Fix:** Rebuilt the entire `bodyParameters` cleanly in the final patch pass.

---

### Execution 1 — Outcome: ISOLATE DONE ✓

Demucs ran for ~17 minutes and completed successfully. Process appeared dead at the 20-minute mark but was actually still running — `stdio: 'ignore'` on the spawned worker means no visible output. Files produced:

```
workspace/2_isolated/smoking_supermarket_s01e01/
  vocals.wav        251 MB
  no_vocals.wav     251 MB
  instrumental.wav  251 MB
  htdemucs/         (raw Demucs tree)
```

WF1 execution 35 errored after 11 seconds — it did NOT wait for Demucs. The poll loop failed immediately.

---

## Execution 2 — 2026-06-13 (re-trigger after isolate done)

### Error 4 — `Check Isolate Status` ep_folder expression corrupted (again)

**Node:** Check Isolate Status  
**Root cause:** The `$node[` prefix was dropped from the ep_folder expression (same f-string quote issue as Error 3). The node was polling with `ep_folder=undefined`, getting no matching status, and the IF condition failing.

**Fix:** Rebuilt ep_folder queryParameter on all 3 status-check nodes cleanly.

### Error 5 — `Check Transcribe Status` and `Check Segment Status` still using `$node["Webhook"]`

**Nodes:** Check Transcribe Status, Check Segment Status  
**Root cause:** These two nodes were missed in previous patches — still referenced the deleted manual trigger node name.

**Fix:** Updated both to `$node['Webhook: harvest-characters']`.

### Status after fixes — Execution 2 in progress

```
isolate:   done  ✓  (skipped — stems already exist)
transcribe: processing  ←  Faster-Whisper running
gpu_lock:  transcribe:smoking_supermarket_s01e01
```

WF1 successfully advanced past isolate into transcribe stage. Monitoring.

---

## CRITICAL BUG FIX — GPU Lock Written by Route (2026-06-13)

**Root cause:** Every GPU route (`isolate`, `transcribe`, `actor`, `dub_song`, `regen_line`, `regen_speaker`) called `fs.writeFileSync(GPU_LOCK, '...')` before spawning the Python worker. The Python worker starts, immediately sees the lock already held, and calls `sys.exit(2)`. The `finally` block that would have cleared the lock never runs. The lock stays forever. No GPU work was ever completing.

**Evidence:**
- After triggering transcribe, only `headroom.exe` Python process was running (MCP server, not our worker)
- `jobs/gpu.lock` contained `transcribe:smoking_supermarket_s01e01` but was written by the route, not Python
- `status_transcribe.json` showed `status: "processing", progress: 0` for 40+ minutes — set by route, never updated

**Fix:** Removed `fs.writeFileSync(GPU_LOCK, ...)` from all 6 GPU routes. Routes now only **check** the lock (returning 409 if held). Python workers are the sole owners of the lock lifecycle — they write it at startup and unlink it in `finally`. Cleared stale `jobs/gpu.lock`. Deleted `test_whisper.py` (temporary diagnostic).

**Files changed:**
- `app/api/isolate/route.ts` — removed write
- `app/api/transcribe/route.ts` — removed write
- `app/api/actor/route.ts` — removed write
- `app/api/dub_song/route.ts` — removed write
- `app/api/regen_line/route.ts` — removed write
- `app/api/regen_speaker/route.ts` — removed write

---

## Error — Segment Lines: "state_director.json has no lines[]" (2026-06-13)

**Root cause:** `bootstrapDirectorFromWhisper` in `segment-lines/route.ts` looked for `raw.segments ?? raw.lines` but `state_whisper.json` is a top-level array (Faster-Whisper's direct output). Both keys were `undefined`, so `segments` became `[]` and `state_director.json` was written with empty `lines[]`. `segment_lines.py` then errored immediately.

**Fix:** Added `Array.isArray(raw) ? raw : (raw.segments ?? raw.lines ?? [])` check in `bootstrapDirectorFromWhisper`. Deleted the bad `state_director.json` and re-triggered — segment lines completed successfully.

---

## Critical Fix — diarize + translate non-blocking (2026-06-13)

**Problem:** WF2's `POST: diarize` node timed out at exactly 300s every run. n8n has a hard 5-minute HTTP Request timeout. The diarize route was synchronous — it called Groq with 422 lines and waited for the full response before returning. Groq + Gemini cascade takes 3-5 minutes for a 422-line episode.

**Fix:** Both `app/api/diarize/route.ts` and `app/api/translate/route.ts` are now non-blocking:
1. Validate inputs synchronously (< 5ms)
2. Idempotency check: if already `done` or `processing`, return immediately
3. Write `status_diarize.json = {status: "processing"}` and return `{status: "processing"}` in < 100ms
4. LLM cascade runs in detached `void (async () => {...})()` background promise
5. On completion: atomically writes `state_director.json` + `status_diarize.json = {status: "done"}`
6. On error: writes `status_diarize.json = {status: "error", error: "..."}` — n8n error branch catches it

n8n's poll loop in WF2 (`IF: diarize done?`) already polls `/api/status` every 8s — no WF2 changes needed.

---

## Stage Tracker — smoking_supermarket_s01e01

```
WF1 - Isolate       - ✅ DONE    output: workspace/2_isolated/smoking_supermarket_s01e01/vocals.wav (251MB)
WF1 - Transcribe    - ✅ DONE    output: jobs/smoking_supermarket_s01e01/state_whisper.json (422 segments, ja)
WF1 - Segment Lines - ✅ DONE    output: jobs/smoking_supermarket_s01e01/line_clips/ (422 WAV clips)
WF1 - Cast Gate     - ⏸ WAITING  (human step: cast review → save_cast → cast_locked=true)

WF2 - Diarize       - 🔄 PROCESSING  Groq llama-3.3-70b running 422 lines in background
WF2 - Cast Review   - ⏳ PENDING  (human step after diarize: review character names in UI)
WF2 - Translate     - ⏳ PENDING
```

**Next human action required:**
After diarize completes → open the Yanflix UI → go to Script Director → review character names and emotion tags → click "Save Cast" → this unlocks WF1 (harvest seeds + clone speakers) and WF2 (translate) simultaneously.

---

## Session 2026-06-14/15 — WF1/WF2/WF3 Full Build & Test

### Error 6 — n8n JS Task Runner crashing under load

**Symptom:** WF1/WF2/WF3 executions (51–54) stuck at `translate=offline` despite translation completing. n8n executions showed nodes hanging indefinitely.

**Root cause:** n8n's JS Task Runner (PID 45 in Docker) crashes under sustained load. Any Code node that uses `$json`, `$node`, or complex expressions sends work to this runner process. When it crashes, all Code nodes in all running workflows time out at 7200s then fail. The runner restarts but running executions are already broken.

**Evidence:** Docker logs showed `Task execution timed out after 7200 seconds` for exec 44 (a Code node from the June 13 session). Runner crashed and never recovered for exec 51–54.

**Fix:** Replaced ALL Code nodes with Set nodes across WF1, WF2, WF3 via n8n REST API (`PUT /api/v1/workflows/{id}`). Set nodes run in the main n8n process and do not use the task runner.
- WF1 (4SimOolWRRLJfWIw): 5 Code nodes → Set nodes — 47→41 nodes
- WF2 (vwHMSQh3MCE0B9GC): 4 Code nodes → Set nodes — 22→19 nodes  
- WF3 (hwxXPnTES4N0Rofm): 7 Code nodes → Set nodes — 63→56 nodes

---

### Error 7 — n8n Wait nodes silently failing (SQLite DB timeouts)

**Symptom:** Workflows would reach a Wait node and never resume, even after the wait interval expired.

**Root cause:** n8n's Wait node (in `timeInterval` mode) uses SQLite to schedule its resume. The SQLite database was experiencing connection timeouts (`Error while saving insights metadata and raw data` flooding the logs). When the scheduler can't write to SQLite, the Wait node's wake-up is never registered and the execution hangs forever.

**Fix:** Removed ALL Wait nodes from WF1/WF2/WF3. Connections that previously ran through a Wait node were rewired directly from the predecessor to the successor (the wait target). This required removing Wait node entries from BOTH the `nodes[]` array AND the `connections{}` dictionary in the workflow JSON before PUTting, otherwise n8n returned `400 — unknown_connection_source`.
- WF1: 6 Wait nodes removed (isolate buffer, transcribe buffer, segment buffer, cast review buffer, harvest buffer, clone buffer)
- WF2: 3 Wait nodes removed (diarize buffer 15s, cast review buffer 30s, translate buffer 10s)
- WF3: 7 Wait nodes removed (translate buffer, intro song buffer, outro song buffer, GPU busy retry, synth buffer, fit buffer, render buffer)

---

### Error 8 — Translate route using Groq (paid, user-prohibited)

**Symptom:** Translation was sending requests to Groq API despite the user explicitly requiring Groq not be used.

**Root cause:** The original `app/api/translate/route.ts` had Groq as the PRIMARY LLM in the cascade, with Gemini as fallback.

**Fix:** Removed entire Groq path. Cascade is now: Gemini key1 (`GEMINI_API_KEY`) → Gemini key2 (`GEMINI_API_KEY_2`) → Ollama (`llama3.1:8b`). Added `INTER_CHUNK_DELAY = 8000ms` between chunks to reduce 429s.

---

### Error 9 — Translate `toArray()` crashing on single-object LLM response

**Symptom:** `[translate] chunk 4 error: LLM returned non-array: {"line_index":150,...}`

**Root cause:** Gemini occasionally returns a single JSON object instead of a JSON array when the chunk has ambiguous structure or the model didn't follow the array instruction.

**Fix:** Updated `toArray()` to detect a single object with `line_index` key and wrap it: `if (raw && typeof raw === 'object' && 'line_index' in raw) return [raw]`. Previously it threw immediately on any non-array response.

---

### Error 10 — `dub_song` returning 404 for episodes with no songs

**Symptom:** WF3 would crash when it called `POST /api/dub_song` with `segment=intro` on an episode that has `songs: []` in `state_director.json`.

**Root cause:** Episode 1 of "Smoking Behind the Supermarket with You" has no songs. The route returned 404 with `{ error: "Song segment 'intro' not found" }`, which n8n treated as a hard error and stopped the workflow.

**Fix:** Route now detects missing song segment and writes a `done/skipped` status file instead of returning 404. Returns `{ status: "done", skipped: true }` so WF3 continues cleanly. Also pre-wrote `status_song_intro.json` and `status_song_outro.json` as done in the job directory.

---

### Error 11 — `synthesize_dub.py` can't find character directories

**Symptom:** Synthesis would fail with `FileNotFoundError: No character dir for 'suzuki_male_supporting' in show 'Smoking Behind the Supermarket with You'`.

**Root cause:** `state_director.json` stores `show_name: "Smoking Behind the Supermarket with You"` (full display name) but character directories are organized under the slugified form: `characters/shows/smoking_behind_the_supermarket_with_you/`.

**Fix:** Updated `locate_char_dir()` in `engine/synthesis/synthesize_dub.py` to try slugified show name as a fallback after exact match fails: `slug = re.sub(r"[^a-z0-9]+", "_", show_name.lower()).strip("_")`.

---

### Error 12 — Song status showing "offline" despite status files existing

**Symptom:** `/api/status?ep_folder=smoking_supermarket_s01e01` returned `status_song_intro: "offline"` even though `status_song_intro.json` existed on disk with `status: "done"`.

**Root cause:** PowerShell 5.1's default file write encoding is UTF-16 LE with BOM. Even `[System.IO.File]::WriteAllText(..., [System.Text.Encoding]::UTF8)` writes a BOM in .NET 4. `JSON.parse` throws a `SyntaxError` when the first character is `﻿`, `readJson()` returns `null`, and `null?.status ?? 'offline'` becomes `"offline"`.

**Fix (two-part):**
1. `app/api/status/route.ts` — `readJson()` now strips BOM before parsing: `if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1)`
2. Song status files rewritten using `[System.Text.UTF8Encoding]::new($false)` (BOM-free UTF-8) in PowerShell

---

### Error 13 — WF3 exec 55 completed "success" without triggering synthesis

**Symptom:** WF3 webhook returned `{"message":"Workflow was started"}`, exec 55 showed `status: "success", finished: true`, but `status_synth_standard.json` was never created and `jobs/gpu.lock` remained free.

**Root cause:** Unknown — n8n's SQLite save failure means exec 55 node data is `[]` (empty). Cannot determine which branch the workflow took. Likely the workflow ran through initial status checks and exited on an early-done path, or the HTTP request to `/api/synth-standard` was never made.

**Workaround:** Triggered synthesis directly via API, bypassing n8n:
```
POST http://localhost:3000/api/synth-standard
{"ep_folder": "smoking_supermarket_s01e01"}
```
IndexTTS2 confirmed running at 07:30:10 — model loaded, synthesizing line 000 onward.

---

### Error 14 — `synth-standard` route uses `conda run -n sonitr` (exec, not detached spawn)

**Note for future:** The route uses `exec()` which buffers all stdout/stderr until process exit. For a 3-4 hour synthesis job this means no streaming logs to the console. The status file is updated after EVERY line (by `synthesize_dub.py` directly) so progress is trackable via `/api/status`, but the Node.js `exec` callback won't fire until synthesis fully completes.

---

## Stage Tracker — smoking_supermarket_s01e01 (2026-06-15)

```
WF0 - Source check   - ✅ DONE    bypass path used
WF1 - Isolate        - ✅ DONE    vocals.wav 251MB
WF1 - Transcribe     - ✅ DONE    state_whisper.json (422 segments, ja)
WF1 - Segment Lines  - ✅ DONE    422 WAV line clips
WF1 - Cast Review    - ✅ DONE    cast_locked=true
WF1 - Harvest Seeds  - ✅ DONE
WF1 - Clone Speakers - ✅ DONE
WF2 - Diarize        - ✅ DONE
WF2 - Translate      - ✅ DONE    410/410 lines merged, 0 missed (23:37:42)
WF3 - Song intro     - ✅ DONE    skipped (no songs in episode)
WF3 - Song outro     - ✅ DONE    skipped (no songs in episode)
WF3 - Synth standard - 🔄 RUNNING IndexTTS2 active, ~3-4h remaining
WF3 - Synth AAVE     - ⏳ PENDING
WF3 - Fit standard   - ⏳ PENDING
WF3 - Fit AAVE       - ⏳ PENDING
WF3 - Render standard- ⏳ PENDING
WF3 - Render AAVE    - ⏳ PENDING
```

---

## Architecture Fix — WF1 Cast-Lock Pause (2026-06-13)

**Problem:** WF1 ran straight through: Segment Lines → Harvest Seeds → Clone Speakers. Harvest Seeds groups clips by character name — but characters aren't assigned until WF2 (diarize → human cast review → save_cast). Running harvest before cast lock means it has no names to group by.

**Correct pipeline order:**
```
WF1:  Isolate → Transcribe → Segment Lines
                                    ↓
WF2:  Diarize → Human cast review → save_cast (sets cast_locked=true) → Translate
                                    ↓
WF1 resumes: Harvest Seeds → Clone Speakers
                                    ↓
WF3:  Synth Standard → Render Standard → Synth AAVE → Render AAVE
```

**Fix:** Inserted 3 nodes between `Is Segmentation Complete?` and `Harvest Seeds`:
- `Wait: cast review buffer` — 30s poll interval
- `GET: cast status` — polls `http://host.docker.internal:3000/api/cast-status?ep_folder=...`
- `IF: cast locked?` — `String($json.cast_locked) == 'true'` → Harvest Seeds; else → loop back to Wait

WF1 now pauses indefinitely at this gate until you complete the cast review in the UI and click Save Cast, which sets `cast_locked: true` in `state_director.json`.

---

