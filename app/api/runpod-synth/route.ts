/**
 * /api/runpod-synth — parallel IndexTTS2 synthesis via RunPod Pod workers
 *
 * Flow per track:
 *   1. Upload state_director.json → R2
 *   2. Zip + upload all ref_*.wav for this show → R2
 *   3. POST /synthesize to each of N Pod workers (default 3) with their line slice
 *   4. Poll all workers every 15s; mirror aggregate progress to status_synth_{track}.json
 *   5. When all workers done: download wav files from R2 → local job dir
 *   6. Write status_synth_{track}.json = done
 *
 * Required env vars:
 *   RUNPOD_POD_URL_1   https://{pod-id}-8000.proxy.runpod.net  (worker 0)
 *   RUNPOD_POD_URL_2   (worker 1)
 *   RUNPOD_POD_URL_3   (worker 2)
 *   CLOUDFLARE_R2_*    (already set)
 */

import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import os from 'os';
import archiver from 'archiver';
import { PutObjectCommand, GetObjectCommand } from '@aws-sdk/client-s3';
import { r2, R2_BUCKET } from '../../../lib/r2';
import { Readable } from 'stream';
import { pipeline } from 'stream/promises';

const POLL_INTERVAL = 15_000;
const WORKER_COUNT  = 3;

function atomicWrite(p: string, data: any) {
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmp, p);
}

function writeStatus(jobDir: string, track: string, payload: object) {
  const p = path.join(jobDir, `status_synth_${track}.json`);
  let cur: any = {};
  if (fs.existsSync(p)) { try { cur = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch {} }
  Object.assign(cur, payload, { updated_at: new Date().toISOString() });
  atomicWrite(p, cur);
}

async function zipDirectory(sourceDir: string, destPath: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const output = fs.createWriteStream(destPath);
    const archive = archiver('zip', { zlib: { level: 6 } });
    output.on('close', resolve);
    archive.on('error', reject);
    archive.pipe(output);
    archive.directory(sourceDir, false);
    archive.finalize();
  });
}

async function uploadToR2(key: string, filePath: string, contentType = 'application/octet-stream') {
  await r2.send(new PutObjectCommand({
    Bucket: R2_BUCKET,
    Key: key,
    Body: fs.createReadStream(filePath),
    ContentType: contentType,
  }));
}

async function downloadFromR2(key: string, destPath: string) {
  const obj = await r2.send(new GetObjectCommand({ Bucket: R2_BUCKET, Key: key }));
  const readable = obj.Body instanceof Readable ? obj.Body : Readable.fromWeb(obj.Body as any);
  await pipeline(readable, fs.createWriteStream(destPath));
}

async function podPost(url: string, body: object): Promise<any> {
  const res = await fetch(`${url}/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`Pod POST failed ${res.status}: ${await res.text()}`);
  return res.json();
}

async function podStatus(url: string): Promise<any> {
  const res = await fetch(`${url}/status`, { signal: AbortSignal.timeout(15_000) });
  if (!res.ok) throw new Error(`Pod status failed ${res.status}`);
  return res.json();
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, track_mode = 'standard', show_slug, worker_count = WORKER_COUNT } = body;

    if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

    const podUrls: string[] = [];
    for (let i = 1; i <= worker_count; i++) {
      const url = process.env[`RUNPOD_POD_URL_${i}`];
      if (!url) return NextResponse.json({ error: `RUNPOD_POD_URL_${i} not set` }, { status: 503 });
      podUrls.push(url.replace(/\/$/, ''));
    }

    const jobDir    = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');
    if (!fs.existsSync(statePath)) {
      return NextResponse.json({ error: 'state_director.json not found' }, { status: 400 });
    }

    // Idempotency
    const statusPath = path.join(jobDir, `status_synth_${track_mode}.json`);
    if (fs.existsSync(statusPath)) {
      try {
        const s = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
        if (s.status === 'done')       return NextResponse.json({ status: 'done', ep_folder, track: track_mode });
        if (s.status === 'processing') return NextResponse.json({ status: 'processing', ep_folder, track: track_mode });
      } catch {}
    }

    writeStatus(jobDir, track_mode, { stage: `synth_${track_mode}`, status: 'processing', progress: 0, engine: 'runpod' });

    // Background worker
    void (async () => {
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'yanflix_runpod_'));
      try {
        const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
        const showName = show_slug || (state.show_name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
        const charsShowDir = path.resolve(`./characters/shows/${showName}`);

        // ── 1. Upload state_director.json ──────────────────────────────────
        const r2StateKey = `runpod/${ep_folder}/state_director.json`;
        await uploadToR2(r2StateKey, statePath, 'application/json');
        console.log(`[runpod-synth] Uploaded state_director.json → ${r2StateKey}`);

        // ── 2. Zip + upload ref wavs ────────────────────────────────────────
        const refsZipPath = path.join(tmp, 'refs.zip');
        if (fs.existsSync(charsShowDir)) {
          await zipDirectory(charsShowDir, refsZipPath);
        } else {
          // Empty zip if no show-specific chars (will fall back to generic in synthesize_dub.py)
          fs.writeFileSync(refsZipPath, Buffer.alloc(0));
        }
        const r2RefsKey = `runpod/${ep_folder}/refs.zip`;
        await uploadToR2(r2RefsKey, refsZipPath);
        console.log(`[runpod-synth] Uploaded refs.zip → ${r2RefsKey}`);

        const r2OutputPrefix = `runpod/${ep_folder}/tts_audio`;

        // ── 3. Submit to each Pod worker ────────────────────────────────────
        const submissions = podUrls.map((url, i) =>
          podPost(url, {
            ep_folder,
            track_mode,
            worker_id: i,
            worker_count,
            r2_state_key:     r2StateKey,
            r2_refs_key:      r2RefsKey,
            r2_output_prefix: r2OutputPrefix,
          }).catch(e => ({ error: e.message, worker_id: i }))
        );
        await Promise.all(submissions);
        console.log(`[runpod-synth] ${worker_count} workers started`);

        // ── 4. Poll until all done ──────────────────────────────────────────
        const workerDone = new Array(worker_count).fill(false);
        const workerError: (string | null)[] = new Array(worker_count).fill(null);

        while (workerDone.some(d => !d)) {
          await new Promise(r => setTimeout(r, POLL_INTERVAL));

          const statuses = await Promise.all(
            podUrls.map((url, i) =>
              workerDone[i]
                ? Promise.resolve({ status: 'done', progress: 100 })
                : podStatus(url).catch(() => ({ status: 'unknown', progress: 0 }))
            )
          );

          let totalProgress = 0;
          let totalSynth    = 0;
          let totalErrors   = 0;
          for (let i = 0; i < worker_count; i++) {
            const s = statuses[i];
            totalProgress += (s.progress ?? 0);
            totalSynth    += (s.synthesized ?? 0);
            totalErrors   += (s.errors ?? 0);
            if (s.status === 'done')  workerDone[i]  = true;
            if (s.status === 'error') workerError[i] = s.error ?? 'unknown error';
          }

          const aggProgress = Math.round(totalProgress / worker_count);
          writeStatus(jobDir, track_mode, {
            progress: aggProgress,
            result: { synthesized: totalSynth, errors: totalErrors },
            workers: statuses.map((s, i) => ({ worker_id: i, ...s })),
          });
          console.log(`[runpod-synth] progress=${aggProgress}% synth=${totalSynth} err=${totalErrors}`);
        }

        const failedWorkers = workerError.filter(Boolean);
        if (failedWorkers.length > 0) {
          throw new Error(`${failedWorkers.length} worker(s) failed: ${failedWorkers.join('; ')}`);
        }

        // ── 5. Download wav files from R2 → local job dir ──────────────────
        console.log(`[runpod-synth] Downloading wav files from R2…`);
        const localOutDir = path.join(jobDir, 'tts_audio', track_mode);
        fs.mkdirSync(localOutDir, { recursive: true });

        // List all objects under the output prefix
        const { ListObjectsV2Command } = await import('@aws-sdk/client-s3');
        let downloaded = 0;
        let contToken: string | undefined;
        do {
          const list: any = await r2.send(new ListObjectsV2Command({
            Bucket: R2_BUCKET,
            Prefix: `${r2OutputPrefix}/${track_mode}/`,
            ContinuationToken: contToken,
          }));
          for (const obj of (list.Contents ?? [])) {
            const fname = path.basename(obj.Key!);
            if (!fname.startsWith('raw_line_') || !fname.endsWith('.wav')) continue;
            await downloadFromR2(obj.Key!, path.join(localOutDir, fname));
            downloaded++;
          }
          contToken = list.NextContinuationToken;
        } while (contToken);

        console.log(`[runpod-synth] Downloaded ${downloaded} wav files`);

        writeStatus(jobDir, track_mode, {
          status: 'done', progress: 100,
          result: { synthesized: downloaded, engine: 'runpod', workers: worker_count },
        });
        console.log(`[runpod-synth] Done — ${ep_folder} ${track_mode}`);

      } catch (e: any) {
        console.error('[runpod-synth] Error:', e.message);
        writeStatus(jobDir, track_mode, { status: 'error', error: e.message });
      } finally {
        try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
      }
    })();

    return NextResponse.json({ status: 'processing', ep_folder, track: track_mode, workers: podUrls.length });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
