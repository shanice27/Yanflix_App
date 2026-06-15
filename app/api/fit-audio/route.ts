import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

// audio_fitter.py is CPU-only — no GPU lock required

function writeStatus(ep_folder: string, track: string, payload: object) {
  const p = path.resolve(`./jobs/${ep_folder}/status_fit_${track}.json`);
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
      return NextResponse.json({ error: 'ep_folder and track_mode are required' }, { status: 400 });
    }
    if (track_mode !== 'standard' && track_mode !== 'aave') {
      return NextResponse.json({ error: "track_mode must be 'standard' or 'aave'" }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    if (!fs.existsSync(path.join(jobDir, 'state_director.json'))) {
      return NextResponse.json({ error: 'state_director.json not found — run Casting Director first' }, { status: 400 });
    }

    // Check if already done (idempotency)
    const statusPath = path.join(jobDir, `status_fit_${track_mode}.json`);
    if (fs.existsSync(statusPath)) {
      try {
        const existing = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
        if (existing.status === 'done') {
          return NextResponse.json({ status: 'done', message: 'Already fitted.', tracking_id: ep_folder });
        }
      } catch {}
    }

    const minRate = body.min_rate ?? 0.7;
    const maxRate = body.max_rate ?? 1.3;
    const threshold = body.mos_flag_threshold ?? 3.2;

    writeStatus(ep_folder, track_mode, {
      stage: `fit_${track_mode}`, status: 'processing', progress: 0,
      error: null, logs: [], owner: body.owner || 'ui',
    });

    // CORRECT: array-form spawn — CPU only, no GPU lock
    const worker = spawn(
      'conda',
      [
        'run', '-n', 'dubbing',
        'python', path.resolve('./engine/audio/audio_fitter.py'),
        '--job_dir', jobDir,
        '--track_mode', track_mode,
        '--min_rate', String(minRate),
        '--max_rate', String(maxRate),
        '--mos_flag_threshold', String(threshold),
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: `Audio fitting initiated for ${track_mode} track.`,
      tracking_id: ep_folder,
      track_mode,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
