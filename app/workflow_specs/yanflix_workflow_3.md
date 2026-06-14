# Yanflix ◆ WF3: Dub Pipeline (Standard + AAVE)

**Read `yanflix_master_spec_v4.md` first. This file contains only the n8n node spec.**

**Purpose:** Runs translation, song dubbing, synthesis, audio fitting, QC, and rendering for BOTH Standard and AAVE tracks. Uses SplitInBatches to run the per-track block twice sequentially.

**Chain:** WF0 → WF1 → WF2 → **WF3**

**Webhook path:** `run-dub`
**Triggered by:** WF2 Node 16 (autopilot) or UI manually (Stage 7 "Run Dub Pipeline" button)
**Total nodes:** 32 + 5 Stop And Error nodes

---

## Structure Overview

```
SHARED PHASE (runs once):
  translate → dub intro song → dub outro song

TRACK PHASE (SplitInBatches, runs twice: standard then aave):
  synthesize → fit audio → QC gate → render video
```

---

## Node Specifications

**All HTTP Request nodes use:** `http://host.docker.internal:3000`
**All POSTs:** `sendBody: true`, `contentType: json`, `bodyParameters` array.

---

## SHARED PHASE

### Node 1 — `Webhook: run-dub`
- Type: Webhook
- HTTP Method: POST
- Path: `run-dub`
- Response Mode: Immediately
- Expected payload:
```json
{
  "show_name":    "...",
  "show_slug":    "crash_landing",
  "episode_id":   "s01e01",
  "ep_folder":    "crash_landing_s01e01",
  "source_lang":  "ko",
  "raw_file_name":"crash_landing_s01e01.mp4",
  "track_modes":  ["standard","aave"],
  "skip_qc_gate": false
}
```

---

### Node 1a — `Set: store context`
- Type: Set
- Fields (store before first Wait):
  - `show_name` → `{{ $json.body.show_name }}`
  - `show_slug` → `{{ $json.body.show_slug }}`
  - `episode_id` → `{{ $json.body.episode_id }}`
  - `ep_folder` → `{{ $json.body.ep_folder }}`
  - `source_lang` → `{{ $json.body.source_lang }}`
  - `track_modes` → `{{ $json.body.track_modes }}`
  - `skip_qc_gate` → `{{ $json.body.skip_qc_gate ?? false }}`

---

### Node 2 — `GET: translate status`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 3 — `IF: translate already done?`
- Type: IF
- Condition: `{{ $json.status_translate }}` equals `done`
- TRUE → Node 7 (skip to song check)
- FALSE → Node 4

---

### Node 4 — `POST: translate`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/translate`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `show_slug` → `{{ $node["Set: store context"].json.show_slug }}`

---

### Node 5 — `Wait: translate buffer`
- Type: Wait / Amount: 8 / Unit: Seconds

---

### Node 5a — `GET: translate poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 5b — `Set: translate poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: translate poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 20) {
  throw new Error(`translate timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_translate } }];
```

---

### Node 6 — `IF: translate done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 7
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: translate error`
  - Message: `translate failed for {{ $node["Set: store context"].json.ep_folder }}`
- else → Node 5 (loop)

---

### Node 7 — `GET: intro song status`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 8 — `IF: intro song already done?`
- Type: IF
- Condition: `{{ $json.status_song_intro }}` equals `done`
- TRUE → Node 12 (skip to outro check)
- FALSE → Node 9

> When `song_source == "cache"` for the intro (Episodes 2+), `/api/dub-song` returns `{status:"done"}` instantly. The poll loop resolves in one cycle — no special n8n logic needed.

---

### Node 9 — `POST: dub intro song`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/dub-song`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `segment` → `intro`
  - `path_mode` → `A`

---

### Node 10 — `Wait: intro song buffer`
- Type: Wait / Amount: 30 / Unit: Seconds

---

### Node 10a — `GET: intro song poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 10b — `Set: intro song poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: intro song poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 120) {
  throw new Error(`intro song timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_song_intro } }];
```

---

### Node 11 — `IF: intro song done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 12
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: song error`
  - Message: `Intro song dub failed for {{ $node["Set: store context"].json.ep_folder }}`
- else → Node 10 (loop)

---

### Node 12 — `POST: dub outro song`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/dub-song`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `segment` → `outro`
  - `path_mode` → `A`

---

### Node 13 — `Wait: outro song buffer`
- Type: Wait / Amount: 30 / Unit: Seconds

---

### Node 13a — `GET: outro song poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 13b — `Set: outro song poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: outro song poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 120) {
  throw new Error(`outro song timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_song_outro } }];
```

---

### Node 13c — `IF: outro song done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 14
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: song error`
- else → Node 13 (loop)

---

## TRACK PHASE

### Node 14 — `Set: track list`
- Type: Set
- Fields:
  - `track_modes` → `{{ $node["Set: store context"].json.track_modes }}`

---

### Node 15 — `SplitInBatches: per track`
- Type: SplitInBatches
- Batch Size: 1
- Input: the `track_modes` array from Node 14 (`["standard","aave"]`)
- "loop" output → Node 16 (start of per-track block)
- "done" output → Node 32 (terminal)

> Each iteration carries the current track string (`"standard"` then `"aave"`) as `{{ $json }}`.

---

### Node 16 — `GET: synth status`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 17 — `IF: synth already done?`
- Type: IF
- Condition: `{{ $json["status_synth_" + $node["SplitInBatches: per track"].json] }}` equals `done`
- TRUE → Node 21 (skip to fit check)
- FALSE → Node 18

---

### Node 18 — `POST: synthesize dub`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/actor`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `track_mode` → `{{ $node["SplitInBatches: per track"].json }}`

---

### Node 19 — `Wait: synth buffer`
- Type: Wait / Amount: 45 / Unit: Seconds

> IndexTTS2 on 300 lines takes 60–90 min. Poll every 45s. Max 200 polls = 150 min ceiling.

---

### Node 19a — `GET: synth poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 19b — `Set: synth poll counter`
- Type: Code (JavaScript)
```javascript
const track = $node["SplitInBatches: per track"].json;
const prev = $node["Set: synth poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 200) { // 200 × 45s = 150 min max
  throw new Error(`synth_${track} timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: {
  poll_count: count,
  status: $json["status_synth_" + track],
  http_status: $json._statusCode ?? 200
}}];
```

---

### Node 20 — `IF: synth done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 21
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: synth error`
  - Message: `synthesize_dub failed for {{ $node["Set: store context"].json.ep_folder }} track {{ $node["SplitInBatches: per track"].json }}`
- Condition C: `{{ $json.http_status }}` equals `409` → TRUE → `Wait: GPU busy retry` (Wait 60s) → Node 18 (retry POST)
- else → Node 19 (loop)

> HTTP 409 = GPU busy (another job running). Wait 60s and retry. This handles the case where the user started a manual synth run from the UI at the same time.

---

### Node 21 — `GET: fit status`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 22 — `IF: fit already done?`
- Type: IF
- Condition: `{{ $json["status_fit_" + $node["SplitInBatches: per track"].json] }}` equals `done`
- TRUE → Node 26 (skip to render check)
- FALSE → Node 23

---

### Node 23 — `POST: fit audio`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/fit-audio`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `track_mode` → `{{ $node["SplitInBatches: per track"].json }}`

---

### Node 24 — `Wait: fit buffer`
- Type: Wait / Amount: 10 / Unit: Seconds

---

### Node 24a — `GET: fit poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 24b — `Set: fit poll counter`
- Type: Code (JavaScript)
```javascript
const track = $node["SplitInBatches: per track"].json;
const prev = $node["Set: fit poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 60) { // 60 × 10s = 10 min max
  throw new Error(`fit_${track} timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: {
  poll_count: count,
  status: $json["status_fit_" + track],
  qc_flagged: $json["fit_result_" + track + "_qc_flagged"] ?? 0
}}];
```

---

### Node 25 — `IF: fit done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 26
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: fit error`
  - Message: `fit_audio failed for {{ $node["Set: store context"].json.ep_folder }} track {{ $node["SplitInBatches: per track"].json }}`
- else → Node 24 (loop)

---

### Node 26 — `GET: render status`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 27 — `IF: render already done?`
- Type: IF
- Condition: `{{ $json["status_render_" + $node["SplitInBatches: per track"].json] }}` equals `done`
- TRUE → back to Node 15 (SplitInBatches next iteration)
- FALSE → Node 27a

---

### Node 27a — `GET: fit result for QC`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 27b — `IF: QC clear to render?`
- Type: IF
- Condition: `{{ $json["fit_result_" + $node["SplitInBatches: per track"].json + "_qc_flagged"] ?? 0 }}` Number equals `0`
- TRUE → Node 28 (proceed to render)
- FALSE:
  - Sub-condition: `{{ $node["Set: store context"].json.skip_qc_gate }}` equals `true`
    - TRUE → Node 28 (draft render, bypass QC)
    - FALSE → `Stop: QC review needed`
      - Message: `Flagged lines found for {{ $node["Set: store context"].json.ep_folder }} track {{ $node["SplitInBatches: per track"].json }}. Open the Script stage in the UI, regenerate red-highlighted lines, then re-trigger WF3. Add skip_qc_gate:true to the payload to force a draft render anyway.`

> `/api/status` must expose `fit_result_{track}_qc_flagged` as a top-level integer key by reading `result.qc_flagged` from `status_fit_{track}.json`.

---

### Node 28 — `POST: render video`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/render`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `track_mode` → `{{ $node["SplitInBatches: per track"].json }}`

---

### Node 29 — `Wait: render buffer`
- Type: Wait / Amount: 15 / Unit: Seconds

---

### Node 29a — `GET: render poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 29b — `Set: render poll counter`
- Type: Code (JavaScript)
```javascript
const track = $node["SplitInBatches: per track"].json;
const prev = $node["Set: render poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 60) { // 60 × 15s = 15 min max
  throw new Error(`render_${track} timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json["status_render_" + track] }}];
```

---

### Node 30 — `IF: render done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → back to Node 15 (SplitInBatches, next track)
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: render error`
  - Message: `render failed for {{ $node["Set: store context"].json.ep_folder }} track {{ $node["SplitInBatches: per track"].json }}`
- else → Node 29 (loop)

---

### Node 32 — `Set: pipeline complete` *(terminal — from SplitInBatches "done" output)*
- Type: Set
- Fields:
  - `status` → `complete`
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `standard_output` → `{{ $node["Set: store context"].json.ep_folder }}_standard.mp4`
  - `aave_output` → `{{ $node["Set: store context"].json.ep_folder }}_aave.mp4`

> Terminal node. No SSE notification. The UI polls `/api/status` and sees render completion from `status_render_standard.json` and `status_render_aave.json` automatically.

---

## Connection Map

```
Node 1   Webhook: run-dub
  ↓
Node 1a  Set: store context
  ↓
Node 2   GET: translate status
  ↓
Node 3   IF: translate already done?
  ├── TRUE  → Node 7
  └── FALSE → Node 4  POST: translate
                ↓ Node 5 Wait 8s ← Node 5b counter ← Node 5a poll
                ↓
              Node 6  IF: translate done? → error/loop/done
                └── done → Node 7

Node 7   GET: intro song status
  ↓
Node 8   IF: intro song already done?
  ├── TRUE  → Node 12
  └── FALSE → Node 9  POST: dub intro song
                ↓ Node 10 Wait 30s ← Node 10b counter ← Node 10a poll
                ↓
              Node 11 IF: intro song done? → error/loop/done
                └── done → Node 12

Node 12  POST: dub outro song
  ↓ Node 13 Wait 30s ← Node 13b counter ← Node 13a poll
  ↓
Node 13c IF: outro song done? → error/loop/done
  └── done → Node 14

Node 14  Set: track list
  ↓
Node 15  SplitInBatches: per track
  ├── "done" → Node 32 (pipeline complete)
  └── "loop" → Node 16  GET: synth status
                  ↓
                Node 17  IF: synth already done?
                  ├── TRUE  → Node 21
                  └── FALSE → Node 18  POST: synthesize dub
                                ↓ Node 19 Wait 45s ← Node 19b counter ← Node 19a poll
                                ↓
                              Node 20  IF: synth done?
                                ├── error  → Stop: synth error
                                ├── 409    → Wait: GPU busy 60s → Node 18
                                ├── loop   → Node 19
                                └── done   → Node 21

                Node 21  GET: fit status
                  ↓
                Node 22  IF: fit already done?
                  ├── TRUE  → Node 26
                  └── FALSE → Node 23  POST: fit audio
                                ↓ Node 24 Wait 10s ← Node 24b counter ← Node 24a poll
                                ↓
                              Node 25  IF: fit done? → error/loop/done
                                └── done → Node 26

                Node 26  GET: render status
                  ↓
                Node 27  IF: render already done?
                  ├── TRUE  → Node 15 (next track)
                  └── FALSE → Node 27a GET: fit result for QC
                                ↓
                              Node 27b IF: QC clear?
                                ├── flagged + skip_qc=false → Stop: QC review needed
                                └── clear (or skip) → Node 28  POST: render video
                                                        ↓ Node 29 Wait 15s ← Node 29b ← Node 29a
                                                        ↓
                                                      Node 30 IF: render done?
                                                        ├── error → Stop: render error
                                                        ├── loop  → Node 29
                                                        └── done  → Node 15 (next track)
Node 32  Set: pipeline complete
```

---

## Stop And Error Nodes Summary

| Node name | Triggered when |
|---|---|
| `Stop: translate error` | translate stage returns error status |
| `Stop: song error` | intro or outro song dub fails |
| `Stop: synth error` | IndexTTS2 synthesis fails |
| `Stop: fit error` | Rubberband fit fails |
| `Stop: QC review needed` | Flagged lines found, skip_qc_gate is false |
| `Stop: render error` | FFmpeg render fails |

---

## Activation Checklist

- [ ] Workflow name is exactly `Yanflix ◆ WF3: Dub Pipeline (Standard + AAVE)`
- [ ] Webhook path is `run-dub`
- [ ] Workflow is **Active**
- [ ] Node 1a stores all context fields including `track_modes` and `skip_qc_gate`
- [ ] **Delete old workflows:** "Yanflix ◆ WF3: Voice Generation & Audio Fitting" and "Yanflix ◆ WF4: Cinematic Video Compositing" if they exist
- [ ] SplitInBatches (Node 15) receives `track_modes` array correctly — test by checking its input in n8n execution view
- [ ] All dynamic status field references use string concatenation: `$json["status_synth_" + $node["SplitInBatches: per track"].json]` — NOT template strings inside f-strings
- [ ] 409 GPU-busy handler on Node 20 tested: trigger a manual synth from UI, then trigger WF3 — confirm WF3 waits and retries
- [ ] QC gate tested: inject a deliberate low-MOS line, confirm `Stop: QC review needed` fires. Re-run with `skip_qc_gate: true` in webhook payload, confirm render proceeds
- [ ] Both tracks complete: `{ep_folder}_standard.mp4` and `{ep_folder}_aave.mp4` in `workspace/5_outputs/`
- [ ] Song vault test (series): Episode 2+ intro/outro song nodes resolve `done` in 1 poll cycle
