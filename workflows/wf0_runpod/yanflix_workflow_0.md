# Yanflix ◆ WF0: Remote Isolate via RunPod

**Read `yanflix_master_spec_v4.md` first. This file contains only the n8n node spec.**

**Purpose:** Offloads Demucs vocal isolation to RunPod L4 GPU Pod, then triggers WF1. This is the entry point for the full autopilot chain.

**Why this workflow exists:** Demucs on the local RTX 4050 takes 15–20 min on a 24-min episode and holds the GPU the entire time. While it runs, NISQA in WF1 falls back to CPU (0.1x speed) and triggers n8n poll timeouts. WF0 runs Demucs on RunPod L4 (~3–5 min), returns stems to local disk, and only then triggers WF1 — GPU is free, NISQA runs at full speed.

**Chain:** WF0 → WF1 → WF2 → WF3

**Webhook path:** `runpod-isolate`
**Total nodes:** 16 + 3 Stop And Error nodes

---

## Required Payload

The UI "Autopilot" button must POST ALL 6 fields. Missing any field is a UI bug.

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

`show_slug` is always explicit ASCII. Never derived from `show_name`. `source_lang`: `ja`, `ko`, `zh`, or `en`.

---

## Next.js Routes Used by This Workflow

These routes live in `app/api/`. n8n calls them — all credentials and file I/O stay on Next.js.

| Route | What it does |
|---|---|
| `GET /api/status` | Check if isolate already done (skip if so) |
| `POST /api/r2-upload` | FFmpeg-extract audio from source video → stream upload to R2 → return `r2_key` |
| `POST /api/runpod-submit` | Submit async Demucs job to RunPod → return `job_id` |
| `GET /api/runpod-poll` | Poll RunPod → return normalized `status` (done/processing/error) + `output` |
| `POST /api/r2-fetch-stems` | Download `vocals.wav` + `instrumental.wav` from R2 → local stable paths → delete R2 keys → write `status_isolate.json` |

See master spec Section 2 for implementation details of each route.

---

## Node Specifications

**All HTTP Request nodes use:** `http://host.docker.internal:3000`
**All body parameters use:** `contentType: json` + individual `bodyParameters` fields (never `specifyBody: json` with `={{ }}` inside string values — n8n Bug 5).
**Always set:** `sendBody: true` on every POST node.

---

### Node 1 — `Webhook: runpod-isolate`
- Type: Webhook
- HTTP Method: POST
- Path: `runpod-isolate`
- Response Mode: Immediately
- Authentication: None

---

### Node 2 — `GET: isolate status check`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query Parameters:
  - `ep_folder` → `{{ $json.body.ep_folder }}`

---

### Node 3 — `IF: isolate already done?`
- Type: IF
- Condition: `{{ $json.status_isolate }}` String equals `done`
- TRUE → Node 14 (skip to WF1 trigger — stems already on disk)
- FALSE → Node 4

---

### Node 4 — `POST: upload to R2`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/r2-upload`
- sendBody: true
- contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `raw_file_name` → `{{ $node["Webhook: runpod-isolate"].json.body.raw_file_name }}`
- **Timeout note:** This call runs FFmpeg extract + R2 upload. Can take 3–8 min for a 1080p episode. Set node timeout to 600000ms (10 min) or leave at n8n default (no timeout). Do NOT set a short timeout here.

---

### Node 5 — `IF: upload ok?`
- Type: IF
- Condition: `{{ $json.status }}` String equals `uploaded`
- TRUE → Node 6
- FALSE → `Stop: R2 upload failed`
  - Type: Stop And Error
  - Error Message: `R2 upload failed for {{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}: {{ $json.error }}`

---

### Node 6 — `POST: submit RunPod job`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/runpod-submit`
- sendBody: true
- contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `task` → `isolate`
  - `r2_key` → `{{ $node["POST: upload to R2"].json.r2_key }}`
  - `source_lang` → `{{ $node["Webhook: runpod-isolate"].json.body.source_lang }}`

---

### Node 7 — `Set: capture job_id`
- Type: Set
- Fields:
  - `runpod_job_id` → `{{ $json.job_id }}`
  - `ep_folder` → `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `show_name` → `{{ $node["Webhook: runpod-isolate"].json.body.show_name }}`
  - `show_slug` → `{{ $node["Webhook: runpod-isolate"].json.body.show_slug }}`
  - `episode_id` → `{{ $node["Webhook: runpod-isolate"].json.body.episode_id }}`
  - `source_lang` → `{{ $node["Webhook: runpod-isolate"].json.body.source_lang }}`
  - `raw_file_name` → `{{ $node["Webhook: runpod-isolate"].json.body.raw_file_name }}`
  - `poll_count` → `0`

> **Why store everything here:** After a Wait node, n8n loses access to earlier node outputs. All context needed by downstream nodes must be stored in a Set node before the first Wait. Reference all downstream values from `$node["Set: capture job_id"].json.*`.

---

### Node 8 — `Wait: RunPod cold start`
- Type: Wait
- Resume: After time interval
- Amount: 60
- Unit: Seconds

> **Why 60s:** RunPod L4 pods have a cold start of 30–60s. The pod must initialize before it starts processing. Polling before cold start completes wastes cycles and can return misleading IN_QUEUE status. If the pod is already warm from recent use, this 60s is a harmless delay.

---

### Node 9 — `GET: RunPod poll`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/runpod-poll`
- Query Parameters:
  - `job_id` → `{{ $node["Set: capture job_id"].json.runpod_job_id }}`

> Always reference `job_id` from `"Set: capture job_id"` node — NOT from the submit response, which is out of scope after Wait nodes.

---

### Node 10 — `Set: increment poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: capture job_id"].json.poll_count ?? 0;
const count = prev + 1;
const MAX_POLLS = 40; // 40 × 15s = 10 minutes max wait

if (count > MAX_POLLS) {
  throw new Error(
    `RunPod isolate timed out after ${count} polls (10 min). ` +
    `Job ID: ${$node["Set: capture job_id"].json.runpod_job_id}. ` +
    `Check RunPod dashboard for status.`
  );
}

return [{
  json: {
    poll_count: count,
    runpod_status: $json.status,
    runpod_job_id: $node["Set: capture job_id"].json.runpod_job_id,
    ep_folder: $node["Set: capture job_id"].json.ep_folder,
    output: $json.output ?? null
  }
}];
```

> The poll counter abort prevents infinite loops if RunPod hangs. Demucs on 24-min audio takes 3–5 min on L4 — 10 min ceiling is generous.

---

### Node 11 — `IF: RunPod done?`
- Type: IF
- Condition A: `{{ $json.runpod_status }}` equals `done` → TRUE → Node 12
- Condition B: `{{ $json.runpod_status }}` equals `error` → TRUE → `Stop: RunPod isolate failed`
  - Error Message: `RunPod Demucs failed for {{ $json.ep_folder }}. Job: {{ $json.runpod_job_id }}. Check RunPod dashboard.`
- else (status = processing) → Node 11a

> `runpod_status` is the normalized value from `/api/runpod-poll` — always `done`, `processing`, or `error`. Never check raw RunPod statuses (COMPLETED, IN_QUEUE, etc.) in n8n IF nodes.

---

### Node 11a — `Wait: RunPod poll interval`
- Type: Wait
- Resume: After time interval
- Amount: 15
- Unit: Seconds
- Connect to: Node 9 (`GET: RunPod poll`)

---

### Node 12 — `POST: fetch stems from R2`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/r2-fetch-stems`
- sendBody: true
- contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: capture job_id"].json.ep_folder }}`

> This route downloads `vocals.wav` + `instrumental.wav` from R2, writes them to `workspace/2_isolated/{ep_folder}/`, verifies file sizes > 0, writes `status_isolate.json`, and deletes all R2 keys (including source audio) in a `finally` block. R2 is always cleaned up even if download fails.

---

### Node 13 — `IF: stems fetched ok?`
- Type: IF
- Condition: `{{ $json.status }}` String equals `done`
- TRUE → Node 14
- FALSE → `Stop: stem fetch failed`
  - Error Message: `Failed to download stems from R2 for {{ $node["Set: capture job_id"].json.ep_folder }}: {{ $json.error }}`

---

### Node 14 — `Wait: pre-WF1 buffer`
- Type: Wait
- Resume: After time interval
- Amount: 3
- Unit: Seconds

> Small buffer to ensure `status_isolate.json` is fully flushed to disk before WF1 reads it on startup.

---

### Node 15 — `POST: trigger WF1`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:5678/webhook/harvest-characters`
- sendBody: true
- contentType: json
- bodyParameters — carry ALL 6 context fields:
  - `show_name` → `{{ $node["Set: capture job_id"].json.show_name }}`
  - `show_slug` → `{{ $node["Set: capture job_id"].json.show_slug }}`
  - `episode_id` → `{{ $node["Set: capture job_id"].json.episode_id }}`
  - `ep_folder` → `{{ $node["Set: capture job_id"].json.ep_folder }}`
  - `source_lang` → `{{ $node["Set: capture job_id"].json.source_lang }}`
  - `raw_file_name` → `{{ $node["Set: capture job_id"].json.raw_file_name }}`

> Missing `source_lang` here would cause WF1 to pass wrong language to Groq Whisper. All 6 fields are mandatory.

---

### Node 16 — `Set: WF0 complete`
- Type: Set
- Fields:
  - `status` → `complete`
  - `ep_folder` → `{{ $node["Set: capture job_id"].json.ep_folder }}`
  - `wf1_triggered` → `true`

---

## Connection Map

```
Node 1  Webhook: runpod-isolate
  ↓
Node 2  GET: isolate status check
  ↓
Node 3  IF: isolate already done?
  ├── TRUE  → Node 14
  └── FALSE → Node 4  POST: upload to R2
                ↓
              Node 5  IF: upload ok?
                ├── FALSE → Stop: R2 upload failed
                └── TRUE  → Node 6  POST: submit RunPod job
                              ↓
                            Node 7  Set: capture job_id
                              ↓
                            Node 8  Wait: 60s cold start
                              ↓
                            Node 9  GET: RunPod poll  ←──────────┐
                              ↓                                   │
                            Node 10 Set: increment poll counter   │
                              ↓                                   │
                            Node 11 IF: RunPod done?              │
                              ├── error   → Stop: RunPod failed   │
                              ├── processing → Node 11a Wait 15s ─┘
                              └── done    → Node 12 POST: fetch stems from R2
                                              ↓
                                            Node 13 IF: stems fetched ok?
                                              ├── FALSE → Stop: stem fetch failed
                                              └── TRUE  → Node 14

Node 14 Wait: 3s buffer
  ↓
Node 15 POST: trigger WF1
  ↓
Node 16 Set: WF0 complete
```

---

## Stop And Error Nodes Summary

| Node name | Triggered when |
|---|---|
| `Stop: R2 upload failed` | `/api/r2-upload` returns non-uploaded status |
| `Stop: RunPod isolate failed` | RunPod job status = error/failed/timeout |
| `Stop: stem fetch failed` | `/api/r2-fetch-stems` returns non-done status |

---

## Activation Checklist

- [ ] Webhook path is `runpod-isolate` (not `harvest-characters` or any other)
- [ ] Workflow is **Active** (toggle in n8n top bar — inactive = webhook never fires)
- [ ] UI Autopilot button POSTs to `http://localhost:5678/webhook/runpod-isolate` (production URL, NOT `webhook-test/...`)
- [ ] Node 7 stores all 6 context fields — verify by test run and checking Set output
- [ ] Node 15 sends all 6 fields to WF1 — missing any breaks downstream workflows
- [ ] Test with Korean show name: `show_slug` in R2 keys should be ASCII (e.g. `crash_landing_s01e01/source_audio.wav`), never Unicode
- [ ] Test isolate-already-done path: run WF0 twice on same episode — second run should skip to Node 14 immediately
