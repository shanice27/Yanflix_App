import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

function writeStatus(ep_folder: string, track: string, payload: object) {
  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  fs.mkdirSync(jobDir, { recursive: true });
  const p = path.join(jobDir, `status_synth_${track}.json`);
  let cur: any = {};
  if (fs.existsSync(p)) { try { cur = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch {} }
  Object.assign(cur, payload, { updated_at: new Date().toISOString() });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cur, null, 2));
  fs.renameSync(tmp, p);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, track_mode } = body;

    if (!ep_folder || !track_mode) {
      return NextResponse.json({ error: 'ep_folder and track_mode required' }, { status: 400 });
    }
    if (track_mode !== 'standard' && track_mode !== 'aave') {
      return NextResponse.json({ error: "track_mode must be 'standard' or 'aave'" }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    if (!fs.existsSync(path.join(jobDir, 'state_director.json'))) {
      return NextResponse.json(
        { error: 'state_director.json not found — run Casting Director first' },
        { status: 400 }
      );
    }

    // Idempotency: already synthesized
    const statusPath = path.join(jobDir, `status_synth_${track_mode}.json`);
    if (fs.existsSync(statusPath)) {
      try {
        const existing = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
        if (existing.status === 'done') {
          return NextResponse.json({ status: 'done', message: 'Already synthesized.', tracking_id: ep_folder });
        }
      } catch {}
    }

    // GPU lock check — 409 if ANY gpu job is running (not just the other track)
    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json(
        { error: `GPU busy — held by: ${holder}` },
        { status: 409 }
      );
    }

    const otherTrack = track_mode === 'standard' ? 'aave' : 'standard';
    const otherStatus = path.join(jobDir, `status_synth_${otherTrack}.json`);
    if (fs.existsSync(otherStatus)) {
      try {
        const s = JSON.parse(fs.readFileSync(otherStatus, 'utf-8'));
        if (s.status === 'processing') {
          return NextResponse.json(
            { error: `${otherTrack} track synthesis is still running` },
            { status: 409 }
          );
        }
      } catch {}
    }

    writeStatus(ep_folder, track_mode, {
      stage: `synth_${track_mode}`, status: 'processing', progress: 0,
      error: null, logs: [], owner: body.owner || 'ui',
    });

    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');

    // CORRECT: array-form spawn
    const worker = spawn(
      'conda',
      [
        'run', '-n', 'dubbing',
        'python', path.resolve('./engine/synthesis/synthesize_dub.py'),
        '--job_dir', jobDir,
        '--track_mode', track_mode,
        '--characters_root', charsRoot,
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: `IndexTTS2 synthesis initiated for ${track_mode} track.`,
      tracking_id: ep_folder,
      track_mode,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
