# Yanflix ◆ WF2: Gemini Script Director

**Read `yanflix_master_spec_v4.md` first. This file contains only the n8n node spec.**

**Purpose:** Transcribes the episode audio via Groq Whisper, runs the Groq/Gemini Director call to propose speaker/character assignments and emotion tags, then PAUSES for human cast review in the UI. After the human saves their cast, WF2 resumes and triggers WF3.

**Chain:** WF0 → WF1 → **WF2** → WF3

**Webhook path:** `script-director`
**Triggered by:** WF1 Node 16 (autopilot) or UI manually (Stage 4 "Run Script Director" button)
**Total nodes:** 20 + 3 Stop And Error nodes

---

## Key Behavior: Human-in-the-Loop Cast Review

WF2 pauses after the Director call and waits for the human to review speaker assignments in the UI and click "Save Cast." The UI calls `/api/save_cast`, which writes `cast_locked: true` to `state_director.json` and sets `status_cast` to `done` in the stage status file. WF2 polls `/api/status` for `status_cast == "done"` before proceeding to WF3.

This means autopilot is NOT fully unattended — cast review is a required human gate. The UI shows a "Waiting for your cast review" banner while WF2 is parked at this gate.

**For shows where cast is already known (Episode 2+):** n8n can bypass this gate by POSTing to `/api/save_cast` automatically with the previous episode's cast data. This is a future enhancement — for now, human review is always required.

---

## LLM Strategy for This Workflow

All LLM calls follow the cascade in master spec Section 6:
1. Groq `llama-3.3-70b-versatile` (primary)
2. Gemini `gemini-2.5-flash` key 1 (fallback)
3. Gemini `gemini-2.5-flash` key 2 (fallback)
4. Ollama `llama3.1:8b` (last resort, only if gpu.lock absent)

The Director prompt (`01_script_director.md`) is a single batched call. Never call per-line.

---

## Node Specifications

**All HTTP Request nodes use:** `http://host.docker.internal:3000`
**All POSTs:** `sendBody: true`, `contentType: json`, `bodyParameters` array.

---

### Node 1 — `Webhook: script-director`
- Type: Webhook
- HTTP Method: POST
- Path: `script-director`
- Response Mode: Immediately
- Expected payload (passed from WF1 Node 16):
```json
{
  "show_name":    "...",
  "show_slug":    "crash_landing",
  "episode_id":   "s01e01",
  "ep_folder":    "crash_landing_s01e01",
  "source_lang":  "ko",
  "raw_file_name":"crash_landing_s01e01.mp4"
}
```

---

### Node 2 — `Set: store context`
- Type: Set
- Fields (store all before first Wait):
  - `show_name` → `{{ $json.body.show_name }}`
  - `show_slug` → `{{ $json.body.show_slug }}`
  - `episode_id` → `{{ $json.body.episode_id }}`
  - `ep_folder` → `{{ $json.body.ep_folder }}`
  - `source_lang` → `{{ $json.body.source_lang }}`
  - `raw_file_name` → `{{ $json.body.raw_file_name }}`

---

### Node 3 — `GET: transcribe status check`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 4 — `IF: transcribe already done?`
- Type: IF
- Condition: `{{ $json.status_transcribe }}` equals `done`
- TRUE → Node 7 (skip to diarize check)
- FALSE → Node 5

---

### Node 5 — `POST: transcribe`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/transcribe`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `source_lang` → `{{ $node["Set: store context"].json.source_lang }}`

> `source_lang` is critical here — it sets the Groq Whisper `language` param. Korean = `ko`, Chinese = `zh`, Japanese = `ja`. Groq Whisper auto-detects if omitted but is faster and more accurate with explicit language.

---

### Node 5a — `Wait: transcribe buffer`
- Type: Wait / Amount: 15 / Unit: Seconds

> Groq Whisper chunked on 24-min audio: ~13 chunks × ~2s/chunk = ~30s total. 15s first poll is appropriate.

---

### Node 5b — `GET: transcribe poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 5c — `Set: transcribe poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: transcribe poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 30) { // 30 × 15s = 7.5 min max (Groq should finish in <2 min)
  throw new Error(`transcribe timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_transcribe } }];
```

---

### Node 6 — `IF: transcribe done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 7
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: transcribe error`
  - Message: `transcribe failed for {{ $node["Set: store context"].json.ep_folder }}. Check if Groq API key is valid and vocals.wav exists.`
- else → Node 5a (loop)

---

### Node 7 — `GET: diarize status check`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 8 — `IF: diarize already done?`
- Type: IF
- Condition: `{{ $json.status_diarize }}` equals `done`
- TRUE → Node 11 (skip to cast check)
- FALSE → Node 9

---

### Node 9 — `POST: diarize`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/diarize`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `show_slug` → `{{ $node["Set: store context"].json.show_slug }}`
  - `source_lang` → `{{ $node["Set: store context"].json.source_lang }}`
  - `use_pyannote` → `false`

> `use_pyannote: false` by default. The Groq Director call handles speaker assignment from transcript context — it's more accurate than frequency-based diarization for scripted content. Set `true` only if running pyannote pre-pass via RunPod (advanced, off by default).

---

### Node 9a — `Wait: diarize buffer`
- Type: Wait / Amount: 20 / Unit: Seconds

> Groq Director call on 300-line transcript: ~3–5s. The 20s buffer accounts for prompt prep, JSON validation, and any retry on malformed output.

---

### Node 9b — `GET: diarize poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 9c — `Set: diarize poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: diarize poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 20) { // 20 × 20s = 6.7 min max
  throw new Error(`diarize timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_diarize } }];
```

---

### Node 10 — `IF: diarize done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 11
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: diarize error`
  - Message: `diarize failed for {{ $node["Set: store context"].json.ep_folder }}. Check Groq API key and transcript file.`
- else → Node 9a (loop)

---

### Node 11 — `GET: cast status check`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 12 — `IF: cast already saved?`
- Type: IF
- Condition: `{{ $json.status_cast }}` equals `done`
- TRUE → Node 15 (skip to WF3 trigger)
- FALSE → Node 13 (begin human-review wait loop)

---

### Node 13 — `Wait: cast review interval`
- Type: Wait / Amount: 30 / Unit: Seconds

> This is the human-in-the-loop gate. The workflow parks here while the human reviews the cast board in the UI and clicks "Save Cast." Poll every 30s — no need to poll faster since this is human-paced. The UI shows a "Waiting for cast review — autopilot paused" banner.

---

### Node 13a — `GET: cast review poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 13b — `Set: cast poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: cast poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
// 240 polls × 30s = 2 hours max — human has 2 hours to review cast
if (count > 240) {
  throw new Error(
    `Cast review timed out after ${count} polls (2 hours) for ` +
    `${$node["Set: store context"].json.ep_folder}. ` +
    `Human must review and save cast in the Speakers stage of the UI.`
  );
}
return [{ json: { poll_count: count, status: $json.status_cast } }];
```

> 240 polls × 30s = 2 hours. Generous but finite — prevents n8n from parking forever if the user abandons a session.

---

### Node 14 — `IF: cast saved?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 15
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: cast error`
  - Message: `save_cast failed for {{ $node["Set: store context"].json.ep_folder }}. Check state_director.json.`
- else → Node 13 (loop — keep waiting for human)

---

### Node 15 — `Wait: pre-WF3 buffer`
- Type: Wait / Amount: 3 / Unit: Seconds

---

### Node 16 — `POST: trigger WF3`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:5678/webhook/run-dub`
- sendBody: true / contentType: json
- bodyParameters — ALL 6 context fields + track modes:
  - `show_name` → `{{ $node["Set: store context"].json.show_name }}`
  - `show_slug` → `{{ $node["Set: store context"].json.show_slug }}`
  - `episode_id` → `{{ $node["Set: store context"].json.episode_id }}`
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `source_lang` → `{{ $node["Set: store context"].json.source_lang }}`
  - `raw_file_name` → `{{ $node["Set: store context"].json.raw_file_name }}`
  - `track_modes` → `["standard","aave"]`

---

### Node 17 — `Set: WF2 complete`
- Type: Set
- Fields:
  - `status` → `complete`
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `wf3_triggered` → `true`

---

## Connection Map

```
Node 1  Webhook: script-director
  ↓
Node 2  Set: store context
  ↓
Node 3  GET: transcribe status check
  ↓
Node 4  IF: transcribe already done?
  ├── TRUE  → Node 7
  └── FALSE → Node 5  POST: transcribe
                ↓
              Node 5a Wait 15s  ←──────────────────────┐
                ↓                                       │
              Node 5b GET: transcribe poll              │
                ↓                                       │
              Node 5c Set: transcribe poll counter      │
                ↓                                       │
              Node 6  IF: transcribe done?              │
                ├── error → Stop: transcribe error      │
                ├── loop  ────────────────────────────────┘
                └── done  → Node 7

Node 7  GET: diarize status check
  ↓
Node 8  IF: diarize already done?
  ├── TRUE  → Node 11
  └── FALSE → Node 9  POST: diarize
                ↓
              Node 9a Wait 20s  ←──────────────────────┐
                ↓                                       │
              Node 9b GET: diarize poll                 │
                ↓                                       │
              Node 9c Set: diarize poll counter         │
                ↓                                       │
              Node 10 IF: diarize done?                 │
                ├── error → Stop: diarize error         │
                ├── loop  ────────────────────────────────┘
                └── done  → Node 11

Node 11 GET: cast status check
  ↓
Node 12 IF: cast already saved?
  ├── TRUE  → Node 15
  └── FALSE → Node 13 Wait: 30s cast review  ←──────────┐
                ↓                                         │
              Node 13a GET: cast review poll              │
                ↓                                         │
              Node 13b Set: cast poll counter             │
                ↓                                         │
              Node 14  IF: cast saved?                    │
                ├── error → Stop: cast error              │
                ├── loop (waiting for human) ──────────────┘
                └── done  → Node 15

Node 15 Wait: 3s buffer
  ↓
Node 16 POST: trigger WF3
  ↓
Node 17 Set: WF2 complete
```

---

## Activation Checklist

- [ ] Workflow name is exactly `Yanflix ◆ WF2: Gemini Script Director`
- [ ] Webhook path is `script-director`
- [ ] Workflow is **Active**
- [ ] Node 2 stores all 6 context fields before any Wait
- [ ] Node 5 passes `source_lang` to `/api/transcribe` — verify Groq Whisper uses correct language
- [ ] Node 9 has `use_pyannote: false` in body parameters
- [ ] Cast review gate (Nodes 13–14): test by running autopilot and confirming workflow parks here. UI should show "Waiting for cast review" banner. Click Save Cast in UI — confirm workflow resumes within one 30s poll cycle.
- [ ] Node 16 passes `track_modes: ["standard","aave"]` — WF3 needs this to run both tracks
- [ ] Node 16 triggers WF3 with all 6 context fields
