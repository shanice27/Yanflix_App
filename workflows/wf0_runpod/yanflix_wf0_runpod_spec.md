# Yanflix — WF0 RunPod Offload & WF1 Fix (Addendum to Master Spec v4)

This document adds to Section 7 and Section 13 of the Master Spec v4.
Hand both documents to Claude Code together.

---

## A. Root Cause: Why WF1 Gets Stuck

Two compounding problems:

**Problem 1 — GPU contention.** When Demucs runs locally, it holds the RTX 4050's 6GB. `harvest_voices.py` then runs NISQA for quality scoring — NISQA tries to use the GPU, finds it busy, falls back to CPU, and runs at 0.1x speed. A 24-minute episode generates 200–400 line clips. NISQA on CPU scores them at ~1 clip/second = 3–7 minutes just for scoring. n8n's poll timeout fires before it finishes.

**Problem 2 — segment_lines.py progress is invisible.** `segment_lines.py` slices the 240MB vocals.wav into 300+ clips using Pydub. It writes nothing to the status file until it's completely done. n8n has no way to know if it's running or dead. If anything interrupts it, the status file never gets written and WF1 polls forever.

**The fix:** WF0 runs Demucs on RunPod L4 FIRST. By the time WF1 starts, Demucs is done, the local GPU is free, and NISQA runs at full speed. WF1 gets fixed poll timeouts and `segment_lines.py` gets incremental progress writes.

---

## B. Multi-Language, Multi-Show Rules

These rules apply to every workflow and every route. They are not show-specific.

### B.1 show_slug — ALWAYS explicit, NEVER derived

Show names in Korean, Chinese, or Japanese contain Unicode characters that break filesystem paths, R2 keys, and slug functions. The `show_slug` is a short ASCII identifier set ONCE at project creation and carried through every webhook payload forever.

```
show_name:  "사랑의 불시착"           → show_slug: "crash_landing"
show_name:  "请回答1988"             → show_slug: "reply_1988"
show_name:  "進撃の巨人"             → show_slug: "attack_on_titan"
show_name:  "Smoking Behind the Supermarket" → show_slug: "smoking_supermarket"
```

`ep_folder` is always: `{show_slug}_{episode_id}` — e.g. `crash_landing_s01e01`

**Every webhook payload must carry:**
```json
{
  "show_name":   "사랑의 불시착",
  "show_slug":   "crash_landing",
  "episode_id":  "s01e01",
  "ep_folder":   "crash_landing_s01e01",
  "source_lang": "ko",
  "raw_file_name": "crash_landing_s01e01.mp4"
}
```

`source_lang` codes: `ja` (Japanese), `ko` (Korean), `zh` (Chinese Mandarin), `en` (English). Passed verbatim to Groq Whisper `language` param. Never hardcode `"ja"`.

### B.2 File paths — always use show_slug

All paths that include a show identifier must use `show_slug`, never `show_name`:

```
characters/shows/{show_slug}/{character}/
characters/shows/{show_slug}/songs/
jobs/{ep_folder}/          ← ep_folder already uses show_slug
```

### B.3 ElevenLabs per-show credit cap

Default 2,000 credits per show (configurable via `ELEVENLABS_CAP_PER_SHOW` in `.env.local`). A Korean drama with 12 characters × 700 credits = 8,400 credits — which would wipe out most of the 130,889 balance. The cap prevents runaway spending on a single show.

`clone_speakers.py` checks cumulative spend for the current `show_slug` in `characters/shows/{show_slug}/credit_log.json` before each ElevenLabs call. Refuses with a clear error if cap would be exceeded.

---

## C. New Next.js Routes Required for RunPod Integration

These routes keep ALL credentials (R2 keys, RunPod API key) on the Next.js server. n8n never sees them — it only calls these routes and receives normalized responses. This is the correct security boundary.

### `POST /api/r2-upload`

Reads the local source file, FFmpeg-extracts audio if needed, streams upload to R2.

```typescript
// app/api/r2-upload/route.ts
export async function POST(req: Request) {
  const { ep_folder, raw_file_name } = await req.json();

  // Source video is always in 0_raw_videos/
  const videoPath = path.join(WORKSPACE_ROOT, "0_raw_videos", raw_file_name);
  if (!fs.existsSync(videoPath)) {
    return Response.json({ error: `Source file not found: ${raw_file_name}` }, { status: 404 });
  }

  // FFmpeg extract audio to temp WAV — handles .mp4, .mkv, .avi, .wav, all formats
  // Converts to mono 44100Hz WAV — Demucs works best at original sample rate
  const tmpWav = path.join(os.tmpdir(), `${ep_folder}_source.wav`);
  await execAsync([
    "ffmpeg", "-y", "-i", videoPath,
    "-vn",                    // no video
    "-acodec", "pcm_s16le",   // WAV format
    "-ar", "44100",           // 44.1kHz — Demucs standard
    "-ac", "2",               // stereo
    tmpWav
  ]);
  // Note: use array form, NOT shell=True — Bug 3 from master spec

  // Upload to R2
  const r2Key = `${ep_folder}/source_audio.wav`;
  const fileStream = fs.createReadStream(tmpWav);
  await r2Client.send(new PutObjectCommand({
    Bucket: process.env.R2_BUCKET,
    Key: r2Key,
    Body: fileStream,
    ContentType: "audio/wav"
  }));

  fs.unlinkSync(tmpWav); // clean up temp file

  return Response.json({ r2_key: r2Key, status: "uploaded" });
}
```

**Why FFmpeg extract first:** A 24-min 1080p MP4 is ~1.5GB. The extracted WAV audio is ~240MB. Uploading 240MB instead of 1.5GB saves ~5 minutes of upload time on a typical home connection.

---

### `POST /api/runpod-submit`

Submits an async job to RunPod. Returns the job_id for n8n to track.

```typescript
// app/api/runpod-submit/route.ts
export async function POST(req: Request) {
  const { ep_folder, task, r2_key, source_lang } = await req.json();

  const endpointId = process.env.RUNPOD_ENDPOINT_ID;
  const res = await fetch(`https://api.runpod.ai/v2/${endpointId}/run`, {
    // Use /run (async), NOT /runsync — runsync has 90s timeout,
    // Demucs on a 24-min file takes 3-5 minutes even on L4
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.RUNPOD_API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      input: {
        task,           // "isolate" or "diarize"
        r2_key,         // R2 key of source file
        ep_folder,      // for R2 output key namespacing
        source_lang     // "ko", "zh", "ja", "en" — passed through for future pyannote lang hints
      }
    })
  });

  if (!res.ok) {
    const err = await res.text();
    return Response.json({ error: `RunPod submit failed: ${err}` }, { status: 502 });
  }

  const data = await res.json();
  // RunPod async /run returns: { id: "job-abc123", status: "IN_QUEUE" }
  return Response.json({ job_id: data.id, status: data.status });
}
```

---

### `GET /api/runpod-poll`

Polls RunPod for job status. Normalizes the response so n8n gets a consistent shape.

```typescript
// app/api/runpod-poll/route.ts
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const jobId = searchParams.get("job_id");
  const endpointId = process.env.RUNPOD_ENDPOINT_ID;

  const res = await fetch(
    `https://api.runpod.ai/v2/${endpointId}/status/${jobId}`,
    { headers: { "Authorization": `Bearer ${process.env.RUNPOD_API_KEY}` } }
  );

  const data = await res.json();

  // RunPod statuses: IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED | CANCELLED | TIMED_OUT
  // Normalize to: processing | done | error
  const normalized = {
    job_id: jobId,
    runpod_status: data.status,
    status: data.status === "COMPLETED" ? "done"
          : data.status === "FAILED" || data.status === "CANCELLED" || data.status === "TIMED_OUT" ? "error"
          : "processing",
    output: data.output ?? null,   // present only when COMPLETED
    error:  data.error  ?? null    // present only when FAILED
  };

  return Response.json(normalized);
}
```

**Critical:** n8n reads `status` (normalized), not `runpod_status`. This shields n8n IF nodes from RunPod's raw status strings changing.

---

### `POST /api/r2-fetch-stems`

Downloads RunPod output files from R2 to local stable paths. Marks isolate done.
Always deletes R2 keys in finally — even on error — to avoid R2 storage accumulation.

```typescript
// app/api/r2-fetch-stems/route.ts
export async function POST(req: Request) {
  const { ep_folder } = await req.json();

  const vocalsKey  = `${ep_folder}/vocals.wav`;
  const instruKey  = `${ep_folder}/instrumental.wav`;
  const stableDir  = path.join(WORKSPACE_ROOT, "2_isolated", ep_folder);
  const vocalsPath = path.join(stableDir, "vocals.wav");
  const instruPath = path.join(stableDir, "instrumental.wav");

  fs.mkdirSync(stableDir, { recursive: true });

  try {
    // Download vocals
    const vocalsObj = await r2Client.send(new GetObjectCommand({
      Bucket: process.env.R2_BUCKET, Key: vocalsKey
    }));
    await streamToFile(vocalsObj.Body, vocalsPath);

    // Download instrumental
    const instruObj = await r2Client.send(new GetObjectCommand({
      Bucket: process.env.R2_BUCKET, Key: instruKey
    }));
    await streamToFile(instruObj.Body, instruPath);

    // Verify files exist and have size > 0
    const vocalsSize = fs.statSync(vocalsPath).size;
    const instruSize = fs.statSync(instruPath).size;
    if (vocalsSize === 0 || instruSize === 0) {
      throw new Error("Downloaded files are empty — RunPod output may be corrupt");
    }

    // Write isolate status as done
    const statusPath = path.join("jobs", ep_folder, "status_isolate.json");
    fs.mkdirSync(path.dirname(statusPath), { recursive: true });
    fs.writeFileSync(statusPath, JSON.stringify({
      stage: "isolate",
      status: "done",
      progress: 100,
      owner: "n8n",
      vocals_path: vocalsPath,
      instrumental_path: instruPath,
      updated_at: new Date().toISOString()
    }));

    return Response.json({ status: "done", vocals_path: vocalsPath, instrumental_path: instruPath });

  } catch (err: any) {
    return Response.json({ error: err.message }, { status: 500 });

  } finally {
    // ALWAYS clean up R2 — even if download failed
    // Errors here are logged but do not override the main response
    try {
      await r2Client.send(new DeleteObjectCommand({ Bucket: process.env.R2_BUCKET, Key: vocalsKey }));
      await r2Client.send(new DeleteObjectCommand({ Bucket: process.env.R2_BUCKET, Key: instruKey }));
      await r2Client.send(new DeleteObjectCommand({ Bucket: process.env.R2_BUCKET, Key: `${ep_folder}/source_audio.wav` }));
    } catch (cleanupErr) {
      console.error("R2 cleanup failed (non-fatal):", cleanupErr);
    }
  }
}
```

---

## D. WORKFLOW: "Yanflix ◆ WF0: Remote Isolate via RunPod"

**Purpose:** Offloads Demucs vocal isolation to RunPod L4, then triggers WF1. Runs before WF1 and WF2. This is the entry point for full autopilot.

**Trigger:** The UI's "Autopilot" button POSTs to this webhook. Previous spec had Autopilot trigger WF1 directly — CHANGE this to trigger WF0 instead. WF0 calls WF1 when done.

**Total nodes: 16 + 3 Stop And Error nodes**

All HTTP Request nodes Base URL: `http://host.docker.internal:3000`

---

### NODE SPEC

**Node 1**
- Name: `Webhook: runpod-isolate`
- Type: Webhook
- HTTP Method: POST
- Path: `runpod-isolate`
- Response Mode: Immediately
- **Required payload fields** (UI must send ALL of these):
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
- **Validation:** If `ep_folder` or `raw_file_name` is missing, the route returns 400. n8n never sees this — it's a bug in the UI payload.

---

**Node 2**
- Name: `GET: isolate status check`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/status`
- Query Parameters:
  - Name: `ep_folder` | Value: `{{ $json.body.ep_folder }}`

---

**Node 3**
- Name: `IF: isolate already done?`
- Type: IF
- Condition: `{{ $json.status_isolate }}` String equals `done`
- TRUE → Node 14 (skip straight to trigger WF1)
- FALSE → Node 4

---

**Node 4**
- Name: `POST: upload to R2`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/r2-upload`
- Send Body: true
- Content Type: JSON
- Body Parameters:
  - `ep_folder` | `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `raw_file_name` | `{{ $node["Webhook: runpod-isolate"].json.body.raw_file_name }}`
- **Note:** This call may take 2–5 minutes (FFmpeg extract + R2 upload). Do NOT set a short timeout. n8n default timeout is fine.

---

**Node 5**
- Name: `IF: upload ok?`
- Type: IF
- Condition: `{{ $json.status }}` String equals `uploaded`
- TRUE → Node 6
- FALSE → `Stop: R2 upload failed`
  - Type: Stop And Error
  - Message: `R2 upload failed: {{ $json.error }}`

---

**Node 6**
- Name: `POST: submit RunPod job`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/runpod-submit`
- Send Body: true
- Content Type: JSON
- Body Parameters:
  - `ep_folder` | `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `task` | `isolate`
  - `r2_key` | `{{ $node["POST: upload to R2"].json.r2_key }}`
  - `source_lang` | `{{ $node["Webhook: runpod-isolate"].json.body.source_lang }}`

---

**Node 7**
- Name: `Set: capture job_id`
- Type: Set
- Fields:
  - `runpod_job_id` | `{{ $json.job_id }}`
  - `ep_folder` | `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
- **Why this node exists:** n8n loses earlier node references once SplitInBatches or Wait nodes reset context. Storing job_id here makes it reliably accessible by name in all downstream nodes.

---

**Node 8**
- Name: `Wait: RunPod cold start`
- Type: Wait
- Resume: After time interval
- Amount: 60 | Unit: Seconds
- **Why 60s not 30s:** RunPod L4 pods have a cold start of 30–60s. If the pod is already warm (recent use), this wait is wasted but harmless. If cold, polling before the pod is ready always returns IN_QUEUE and wastes poll cycles.

---

**Node 9**
- Name: `GET: RunPod poll`
- Type: HTTP Request
- Method: GET
- URL: `http://host.docker.internal:3000/api/runpod-poll`
- Query Parameters:
  - `job_id` | `{{ $node["Set: capture job_id"].json.runpod_job_id }}`
- **Always reference job_id from "Set: capture job_id" node, not the submit response.**
  The submit response is no longer in scope after Wait nodes.

---

**Node 10**
- Name: `Set: poll counter`
- Type: Code
- JavaScript:
```javascript
// Increment poll counter — abort after 40 polls (40 × 15s = 10 minutes max)
// Demucs on 24-min audio takes 3-5 min on L4 — 10 min is generous
const prev = $node["Set: poll counter"]?.json?.poll_count ?? 0;
const count = prev + 1;

if (count > 40) {
  throw new Error(`RunPod isolate timed out after ${count} polls (10 min). Check RunPod dashboard for job: ${$node["Set: capture job_id"].json.runpod_job_id}`);
}

return [{ json: {
  poll_count: count,
  runpod_status: $json.status,
  runpod_job_id: $node["Set: capture job_id"].json.runpod_job_id,
  ep_folder: $node["Set: capture job_id"].json.ep_folder,
  output: $json.output
}}];
```

---

**Node 11**
- Name: `IF: RunPod done?`
- Type: IF
- Condition A: `{{ $json.runpod_status }}` String equals `done`
  - TRUE → Node 12
- Condition B: `{{ $json.runpod_status }}` String equals `error`
  - TRUE → `Stop: RunPod isolate failed`
    - Type: Stop And Error
    - Message: `RunPod Demucs failed for {{ $node["Set: capture job_id"].json.ep_folder }}. Job: {{ $json.runpod_job_id }}`
- else (processing / IN_QUEUE / IN_PROGRESS) → Node 11a

**Node 11a**
- Name: `Wait: RunPod poll interval`
- Type: Wait
- Amount: 15 | Unit: Seconds
- Connect back to: Node 9 (`GET: RunPod poll`)

---

**Node 12**
- Name: `POST: fetch stems from R2`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:3000/api/r2-fetch-stems`
- Send Body: true
- Content Type: JSON
- Body Parameters:
  - `ep_folder` | `{{ $node["Set: capture job_id"].json.ep_folder }}`

---

**Node 13**
- Name: `IF: stems fetched ok?`
- Type: IF
- Condition: `{{ $json.status }}` String equals `done`
- TRUE → Node 14
- FALSE → `Stop: stem fetch failed`
  - Type: Stop And Error
  - Message: `Failed to download stems from R2: {{ $json.error }}`

---

**Node 14**
- Name: `Wait: pre-WF1 buffer`
- Type: Wait
- Amount: 3 | Unit: Seconds
- **Why:** Small buffer to ensure status_isolate.json is flushed to disk before WF1 reads it.

---

**Node 15**
- Name: `POST: trigger WF1`
- Type: HTTP Request
- Method: POST
- URL: `http://host.docker.internal:5678/webhook/harvest-characters`
- Send Body: true
- Content Type: JSON
- Body Parameters — carry FULL context, every field:
  - `show_name` | `{{ $node["Webhook: runpod-isolate"].json.body.show_name }}`
  - `show_slug` | `{{ $node["Webhook: runpod-isolate"].json.body.show_slug }}`
  - `episode_id` | `{{ $node["Webhook: runpod-isolate"].json.body.episode_id }}`
  - `ep_folder` | `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `source_lang` | `{{ $node["Webhook: runpod-isolate"].json.body.source_lang }}`
  - `raw_file_name` | `{{ $node["Webhook: runpod-isolate"].json.body.raw_file_name }}`
- **Do NOT use f-string interpolation in n8n expression strings. Use the bodyParameters array with individual named fields.**

---

**Node 16**
- Name: `Set: WF0 complete`
- Type: Set
- Fields:
  - `status` | `complete`
  - `ep_folder` | `{{ $node["Webhook: runpod-isolate"].json.body.ep_folder }}`
  - `wf1_triggered` | `true`

---

### WF0 Connection Map

```
Node 1 (Webhook)
  → Node 2 (GET status)
  → Node 3 (IF already done?)
      TRUE  → Node 14 (Wait buffer) → Node 15 (Trigger WF1) → Node 16
      FALSE → Node 4 (POST r2-upload)
                → Node 5 (IF upload ok?)
                    FALSE → Stop: R2 upload failed
                    TRUE  → Node 6 (POST runpod-submit)
                              → Node 7 (Set: capture job_id)
                                → Node 8 (Wait: cold start 60s)
                                  → Node 9 (GET: RunPod poll)
                                    → Node 10 (Set: poll counter)
                                      → Node 11 (IF: RunPod done?)
                                          error  → Stop: RunPod isolate failed
                                          loop   → Node 11a (Wait 15s) → Node 9
                                          done   → Node 12 (POST r2-fetch-stems)
                                                    → Node 13 (IF stems ok?)
                                                        FALSE → Stop: stem fetch failed
                                                        TRUE  → Node 14 → Node 15 → Node 16
```

---

## E. WF1 Fixes: Why It Gets Stuck and How to Fix It

### E.1 Change WF1 trigger — receive from WF0, not UI

WF1's webhook `harvest-characters` is now called by WF0 Node 15, not the UI directly. The payload shape is identical — WF1 does not need to know it came from WF0.

**Update the UI Autopilot button:** change the POST target from `harvest-characters` to `runpod-isolate`. WF0 → WF1 is now the chain. Users who want to skip isolation (stems already done) can still trigger WF1 directly from the UI's Stage 4 manual button.

### E.2 Fix segment_lines.py — write progress incrementally

`segment_lines.py` currently writes nothing until complete. Add progress writes every 50 lines:

```python
# segment_lines.py — add inside the line slicing loop
total_lines = len(transcript_segments)
for i, seg in enumerate(transcript_segments):
    # ... slice and save clip ...

    # Write progress every 50 lines or on last line
    if i % 50 == 0 or i == total_lines - 1:
        write_status("segment", "processing",
                     progress=int((i / total_lines) * 100),
                     logs=[f"Sliced {i+1}/{total_lines} clips"])
```

### E.3 Fix WF1 poll timeouts

Current WF1 poll waits are too short for a 24-minute episode. Updated values:

| Stage | Old wait | New wait | Max polls | Max time |
|---|---|---|---|---|
| segment_lines | 8s | 20s | 60 | 20 min |
| harvest_voices (NISQA) | 8s | 20s | 90 | 30 min |
| clone_speakers (ElevenLabs) | 8s | 15s | 60 | 15 min |

### E.4 Fix harvest_voices.py — GPU-aware NISQA

NISQA should not attempt GPU if gpu.lock is held:

```python
# harvest_voices.py
import os

def get_nisqa_device():
    lock_path = os.path.join("jobs", "gpu.lock")
    if os.path.exists(lock_path):
        print("GPU lock held — running NISQA on CPU")
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"

device = get_nisqa_device()
nisqa_model = NISQA(device=device)
```

Since WF0 runs Demucs on RunPod (no local GPU lock), by the time WF1 runs the GPU is always free and NISQA gets CUDA.

### E.5 Fix ElevenLabs per-show credit tracking

Add `credit_log.json` to the show folder. `clone_speakers.py` reads and writes it:

```python
# clone_speakers.py
import json, os

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
            f"Spent so far: {log['total_spent']}. "
            f"Estimated cost: {estimated_cost}. "
            f"Raise ELEVENLABS_CAP_PER_SHOW in .env.local to continue."
        )

def record_credit_spend(show_slug: str, character: str, spent: int) -> None:
    log_path = f"characters/shows/{show_slug}/credit_log.json"
    log = {"total_spent": 0, "characters": {}}
    if os.path.exists(log_path):
        with open(log_path) as f:
            log = json.load(f)

    log["total_spent"] += spent
    log["characters"][character] = log["characters"].get(character, 0) + spent

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
```

---

## F. RunPod Handler Update (pyannote fix confirmed)

The `runpod_handler.py` from Section 13 of the master spec is correct. Confirmed working pattern for pyannote `DiarizeOutput`:

```python
# CONFIRMED WORKING — do not change this pattern
result = pipeline({"waveform": waveform, "sample_rate": 16000})
annotation = result.speaker_diarization   # ← must access .speaker_diarization
segments = [
    {"start": round(t.start, 3), "end": round(t.end, 3), "speaker": s}
    for t, _, s in annotation.itertracks(yield_label=True)
]
```

Common wrong patterns that fail:
```python
# WRONG — DiarizeOutput is not directly iterable
for seg in result: ...

# WRONG — DiarizeOutput has no .segments attribute
for seg in result.segments: ...

# WRONG — old API, removed in recent pyannote
result = pipeline(path)  # use_auth_token=... also removed
```

---

## G. Updated `.env.local` additions

```bash
# RunPod
RUNPOD_ENABLED=true
RUNPOD_API_KEY=
RUNPOD_ENDPOINT_ID=
# (set both of these after flash deploy returns your endpoint)

# Cloudflare R2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=yanflix-audio

# ElevenLabs
ELEVENLABS_CAP_PER_SHOW=2000
# Increase per show if needed. 130,889 credits ÷ 700/char = ~187 characters total budget.
# A typical Korean drama has 8-15 recurring characters needing full banks.
```

---

## H. Build Checklist Additions (append to Section 11)

* [ ] WF0 deployed and Active in n8n. Path `runpod-isolate` responds to POST.
* [ ] UI Autopilot button POSTs to `http://localhost:5678/webhook/runpod-isolate` (not harvest-characters).
* [ ] `/api/r2-upload` tested with a Korean/Chinese filename — confirms no unicode path errors.
* [ ] `/api/runpod-submit` returns a `job_id` string (not null) within 5s.
* [ ] `/api/runpod-poll` normalizes COMPLETED → `done`, FAILED → `error`, IN_QUEUE → `processing`.
* [ ] `/api/r2-fetch-stems` deletes R2 keys even when download throws an error (test by cutting network mid-download).
* [ ] WF0 Node 7 (`Set: capture job_id`) — confirm `runpod_job_id` is accessible in Node 9 expression after a Wait node.
* [ ] WF0 Node 15 triggers WF1 with all 6 context fields. WF1 receives `source_lang` correctly.
* [ ] WF1 re-tested with `source_lang: "ko"` payload — Groq Whisper uses Korean language param.
* [ ] `harvest_voices.py` NISQA runs on CUDA when WF0 completes (gpu.lock absent).
* [ ] `clone_speakers.py` credit cap enforced — test with CAP=10, verify refusal error before API call.
* [ ] `credit_log.json` written to `characters/shows/{show_slug}/` not `characters/shows/{show_name}/`.
* [ ] All show folder paths use `show_slug` (ASCII). No unicode in filesystem paths.
* [ ] Episode 1 of Korean drama processed end-to-end. Check `ep_folder` uses slug not Korean chars.
