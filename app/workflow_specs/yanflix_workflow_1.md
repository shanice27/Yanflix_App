# Yanflix ◆ WF1: Character Vault & Voice Bank

**Read `yanflix_master_spec_v4.md` first. This file contains only the n8n node spec.**

**Purpose:** After Demucs isolation (WF0), this workflow slices vocals into per-line clips, NISQA-scores them, harvests character seeds into ChromaDB, and builds ElevenLabs emotional reference banks for new characters. Then triggers WF2.

**Chain:** WF0 → **WF1** → WF2 → WF3

**Webhook path:** `harvest-characters`
**Triggered by:** WF0 Node 15 (autopilot) or UI manually (Stage 4 "Run Character Harvest" button)
**Total nodes:** 18 + 3 Stop And Error nodes

---

## Why WF1 Was Getting Stuck (Fixed)

**Problem 1 — GPU contention:** When Demucs ran locally, it held the RTX 4050 GPU. NISQA then fell back to CPU, scoring clips at ~0.1× speed. For a 24-min episode with 300+ clips, that's 5–7 min just for NISQA. n8n poll timeouts fired before completion.

**Fix:** WF0 now runs Demucs on RunPod L4 FIRST. By the time WF1 starts, the local GPU is completely free. NISQA runs at full CUDA speed.

**Problem 2 — `segment_lines.py` wrote no progress updates.** n8n could not distinguish "still running" from "crashed." Timeouts defaulted to assuming crash.

**Fix:** `segment_lines.py` now writes incremental progress to `status_segment.json` every 50 lines. Updated poll wait times in this workflow reflect real timing.

---

## Python Worker Requirements

These fixes must be implemented in the Python workers before wiring this workflow.

### `segment_lines.py` — incremental progress writes

```python
# Inside the line-slicing loop — add progress write every 50 lines
total = len(transcript_segments)
for i, seg in enumerate(transcript_segments):
    # ... slice clip, save to line_clips/ ...
    if i % 50 == 0 or i == total - 1:
        write_status("segment", "processing",
                     progress=int((i / total) * 100),
                     logs=[f"Sliced {i+1}/{total} clips"])
# After loop:
write_status("segment", "done", progress=100)
```

### `harvest_voices.py` — GPU-aware NISQA

```python
def get_nisqa_device():
    # Only attempt GPU if lock is free — WF0 handles Demucs on RunPod so
    # lock should always be free here, but check defensively
    if os.path.exists(os.path.join("jobs", "gpu.lock")):
        print("GPU lock held — NISQA running on CPU")
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"

device = get_nisqa_device()
nisqa_model = NISQA(device=device)
```

### `clone_speakers.py` — per-show credit cap

```python
def check_credit_cap(show_slug: str, estimated_cost: int) -> None:
    cap = int(os.environ.get("ELEVENLABS_CAP_PER_SHOW", 2000))
    log_path = f"characters/shows/{show_slug}/credit_log.json"
    log = {"total_spent": 0, "characters": {}}
    if os.path.exists(log_path):
        with open(log_path) as f:
            log = json.load(f)
    if log["total_spent"] + estimated_cost > cap:
        raise ValueError(
            f"ElevenLabs cap ({cap} credits/show) would be exceeded. "
            f"Spent: {log['total_spent']}. Estimated: {estimated_cost}. "
            f"Raise ELEVENLABS_CAP_PER_SHOW in .env.local to continue."
        )

def record_credit_spend(show_slug: str, character: str, spent: int) -> None:
    log_path = f"characters/shows/{show_slug}/credit_log.json"
    log = {"total_spent": 0, "characters": {}}
    if os.path.exists(log_path):
        with open(log_path) as f: log = json.load(f)
    log["total_spent"] += spent
    log["characters"][character] = log["characters"].get(character, 0) + spent
    with open(log_path, "w") as f: json.dump(log, f, indent=2)
```

---

## Node Specifications

**All HTTP Request nodes use:** `http://host.docker.internal:3000`
**All POSTs:** `sendBody: true`, `contentType: json`, `bodyParameters` array.

---

### Node 1 — `Webhook: harvest-characters`
- Type: Webhook
- HTTP Method: POST
- Path: `harvest-characters`
- Response Mode: Immediately
- Expected payload (passed from WF0 Node 15):
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
- Fields — store all payload fields immediately, before any Wait nodes:
  - `show_name` → `{{ $json.body.show_name }}`
  - `show_slug` → `{{ $json.body.show_slug }}`
  - `episode_id` → `{{ $json.body.episode_id }}`
  - `ep_folder` → `{{ $json.body.ep_folder }}`
  - `source_lang` → `{{ $json.body.source_lang }}`
  - `raw_file_name` → `{{ $json.body.raw_file_name }}`

---

### Node 3 — `GET: segment status check`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 4 — `IF: segment already done?`
- Type: IF
- Condition: `{{ $json.status_segment }}` equals `done`
- TRUE → Node 7 (skip to harvest check)
- FALSE → Node 5

---

### Node 5 — `POST: segment lines`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/segment`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 5a — `Wait: segment buffer`
- Type: Wait / Amount: 20 / Unit: Seconds

---

### Node 5b — `GET: segment poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 5c — `Set: segment poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: segment poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 60) { // 60 × 20s = 20 min max
  throw new Error(`segment_lines timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_segment } }];
```

---

### Node 6 — `IF: segment done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 7
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: segment error`
  - Message: `segment_lines.py failed for {{ $node["Set: store context"].json.ep_folder }}`
- else → Node 5a (loop)

---

### Node 7 — `GET: harvest status check`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 8 — `IF: harvest already done?`
- Type: IF
- Condition: `{{ $json.status_harvest }}` equals `done`
- TRUE → Node 11 (skip to clone check)
- FALSE → Node 9

---

### Node 9 — `POST: harvest voices`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/save_speaker_to_vault`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `show_slug` → `{{ $node["Set: store context"].json.show_slug }}`

---

### Node 9a — `Wait: harvest buffer`
- Type: Wait / Amount: 20 / Unit: Seconds

> Harvest runs NISQA on 300+ clips. With GPU free (WF0 handled Demucs on RunPod), NISQA on CUDA scores ~10 clips/sec = ~30s for 300 clips. 20s poll interval is appropriate.

---

### Node 9b — `GET: harvest poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 9c — `Set: harvest poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: harvest poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 90) { // 90 × 20s = 30 min max
  throw new Error(`harvest_voices timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_harvest } }];
```

---

### Node 10 — `IF: harvest done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 11
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: harvest error`
  - Message: `harvest_voices.py failed for {{ $node["Set: store context"].json.ep_folder }}`
- else → Node 9a (loop)

---

### Node 11 — `GET: clone status check`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 12 — `IF: clone already done?`
- Type: IF
- Condition: `{{ $json.status_clone }}` equals `done`
- TRUE → Node 15 (skip to WF2 trigger — all banks already built)
- FALSE → Node 13

---

### Node 13 — `POST: clone speakers`
- Type: HTTP Request / Method: POST
- URL: `http://host.docker.internal:3000/api/clone_speakers`
- sendBody: true / contentType: json
- bodyParameters:
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `show_slug` → `{{ $node["Set: store context"].json.show_slug }}`

---

### Node 13a — `Wait: clone buffer`
- Type: Wait / Amount: 15 / Unit: Seconds

> ElevenLabs IVC clone + 7 TTS calls per character. For a show with 6 characters = ~42 API calls. At ~3s/call = ~2 min. 15s poll is appropriate.
> Episode 2+ of the same show: ChromaDB recognizes all characters → clone finishes in seconds.

---

### Node 13b — `GET: clone poll`
- Type: HTTP Request / Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query: `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`

---

### Node 13c — `Set: clone poll counter`
- Type: Code (JavaScript)
```javascript
const prev = $node["Set: clone poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;
if (count > 60) { // 60 × 15s = 15 min max
  throw new Error(`clone_speakers timed out after ${count} polls for ${$node["Set: store context"].json.ep_folder}`);
}
return [{ json: { poll_count: count, status: $json.status_clone } }];
```

---

### Node 14 — `IF: clone done?`
- Type: IF
- Condition A: `{{ $json.status }}` equals `done` → TRUE → Node 15
- Condition B: `{{ $json.status }}` equals `error` → TRUE → `Stop: clone error`
  - Message: `clone_speakers failed for {{ $node["Set: store context"].json.ep_folder }}. Check credit_log.json — may have hit per-show ElevenLabs cap.`
- else → Node 13a (loop)

---

### Node 15 — `Wait: pre-WF2 buffer`
- Type: Wait / Amount: 3 / Unit: Seconds

---

### Node 16 — `POST: trigger WF2`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:5678/webhook/script-director`
- sendBody: true / contentType: json
- bodyParameters — ALL 6 context fields:
  - `show_name` → `{{ $node["Set: store context"].json.show_name }}`
  - `show_slug` → `{{ $node["Set: store context"].json.show_slug }}`
  - `episode_id` → `{{ $node["Set: store context"].json.episode_id }}`
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `source_lang` → `{{ $node["Set: store context"].json.source_lang }}`
  - `raw_file_name` → `{{ $node["Set: store context"].json.raw_file_name }}`

---

### Node 17 — `Set: WF1 complete`
- Type: Set
- Fields:
  - `status` → `complete`
  - `ep_folder` → `{{ $node["Set: store context"].json.ep_folder }}`
  - `wf2_triggered` → `true`

---

## Connection Map

```
Node 1  Webhook: harvest-characters
  ↓
Node 2  Set: store context
  ↓
Node 3  GET: segment status check
  ↓
Node 4  IF: segment already done?
  ├── TRUE  → Node 7
  └── FALSE → Node 5  POST: segment lines
                ↓
              Node 5a Wait 20s  ←────────────────────────┐
                ↓                                         │
              Node 5b GET: segment poll                   │
                ↓                                         │
              Node 5c Set: segment poll counter           │
                ↓                                         │
              Node 6  IF: segment done?                   │
                ├── error → Stop: segment error           │
                ├── loop  ─────────────────────────────────┘
                └── done  → Node 7

Node 7  GET: harvest status check
  ↓
Node 8  IF: harvest already done?
  ├── TRUE  → Node 11
  └── FALSE → Node 9  POST: harvest voices
                ↓
              Node 9a Wait 20s  ←───────────────────────┐
                ↓                                        │
              Node 9b GET: harvest poll                  │
                ↓                                        │
              Node 9c Set: harvest poll counter          │
                ↓                                        │
              Node 10 IF: harvest done?                  │
                ├── error → Stop: harvest error          │
                ├── loop  ────────────────────────────────┘
                └── done  → Node 11

Node 11 GET: clone status check
  ↓
Node 12 IF: clone already done?
  ├── TRUE  → Node 15
  └── FALSE → Node 13 POST: clone speakers
                ↓
              Node 13a Wait 15s  ←──────────────────────┐
                ↓                                        │
              Node 13b GET: clone poll                   │
                ↓                                        │
              Node 13c Set: clone poll counter           │
                ↓                                        │
              Node 14  IF: clone done?                   │
                ├── error → Stop: clone error            │
                ├── loop  ────────────────────────────────┘
                └── done  → Node 15

Node 15 Wait: 3s buffer
  ↓
Node 16 POST: trigger WF2
  ↓
Node 17 Set: WF1 complete
```

---

## Activation Checklist

- [ ] Webhook path is `harvest-characters`
- [ ] Workflow is **Active**
- [ ] Node 2 (`Set: store context`) stores all 6 fields — test run and verify Set output
- [ ] `segment_lines.py` writes incremental progress every 50 lines (or poll timeouts will fire)
- [ ] `harvest_voices.py` checks gpu.lock before choosing NISQA device
- [ ] `clone_speakers.py` reads/writes `credit_log.json` for per-show cap enforcement
- [ ] Test Episode 2 of same show: clone stage should resolve `done` in seconds (ChromaDB hit, 0 credits)
- [ ] Test with Korean drama: confirm `show_slug` in all API bodies is ASCII, not Korean characters
- [ ] Node 16 triggers WF2 with all 6 context fields including `source_lang`
