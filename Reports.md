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

