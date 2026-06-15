import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

// Render is CPU-heavy FFmpeg — no GPU lock (video stream is copied, not re-encoded)

function writeStatus(ep_folder: string, track: string, payload: object) {
  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  fs.mkdirSync(jobDir, { recursive: true });
  const p = path.join(jobDir, `status_render_${track}.json`);
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
      return NextResponse.json({ error: 'state_director.json not found' }, { status: 400 });
    }

    // Idempotency: already rendered
    const statusPath = path.join(jobDir, `status_render_${track_mode}.json`);
    const outputPath = path.resolve(`./workspace/5_outputs/${ep_folder}_${track_mode}.mp4`);
    if (fs.existsSync(statusPath) && fs.existsSync(outputPath)) {
      try {
        const s = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
        if (s.status === 'done') {
          return NextResponse.json({ status: 'done', message: 'Already rendered.', tracking_id: ep_folder, output: outputPath });
        }
      } catch {}
    }

    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');
    const showName = body.show_name || '';

    writeStatus(ep_folder, track_mode, {
      stage: `render_${track_mode}`, status: 'processing', progress: 0,
      error: null, logs: [], owner: body.owner || 'ui',
    });

    // CORRECT: array-form spawn — FFmpeg path never goes through shell
    const worker = spawn(
      'conda',
      [
        'run', '-n', 'dubbing',
        'python', path.resolve('./engine/rendering/render_video.py'),
        '--job_dir', jobDir,
        '--track_mode', track_mode,
        '--show', showName,
        '--characters_root', charsRoot,
        '--output_dir', path.resolve('./workspace/5_outputs'),
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: `Final render initiated for ${track_mode} track.`,
      tracking_id: ep_folder,
      track_mode,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
