import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

function writeStatus(ep_folder: string, payload: object) {
  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  fs.mkdirSync(jobDir, { recursive: true });
  const p = path.join(jobDir, 'status_isolate.json');
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
    const ep_folder = (body.ep_folder || '').trim();
    // Accept video_path (from n8n autopilot) or audio (legacy)
    const rawPath = body.video_path || body.audio || '';

    if (!ep_folder || !rawPath) {
      return NextResponse.json({ error: 'ep_folder and video_path required' }, { status: 400 });
    }

    const filePath = path.resolve(rawPath);
    if (!fs.existsSync(filePath)) {
      return NextResponse.json(
        { error: `File not found: ${rawPath} — upload the source file first` },
        { status: 400 }
      );
    }

    const ep_dir = path.resolve(`./workspace/2_isolated/${ep_folder}`);
    const stableVocals = path.join(ep_dir, 'vocals.wav');
    const stableBg = path.join(ep_dir, 'no_vocals.wav');

    // Idempotency: stems already exist
    if (fs.existsSync(stableVocals) && fs.existsSync(stableBg)) {
      writeStatus(ep_folder, { stage: 'isolate', status: 'done', progress: 100, error: null });
      return NextResponse.json({ status: 'done', message: 'Already isolated.', tracking_id: ep_folder });
    }

    // GPU lock check
    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json(
        { error: `GPU busy — held by: ${holder}` },
        { status: 409 }
      );
    }

    // Lock is written by the Python worker, not the route
    fs.mkdirSync(path.dirname(GPU_LOCK), { recursive: true });
    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    fs.mkdirSync(jobDir, { recursive: true });
    writeStatus(ep_folder, {
      stage: 'isolate', status: 'processing', progress: 0,
      error: null, logs: [], owner: body.owner || 'ui',
    });

    // CORRECT: array-form spawn — brackets/spaces in filenames safe, shell never sees the path
    const worker = spawn(
      'conda',
      [
        'run', '-n', 'dubbing',
        'python', path.resolve('./engine/audio/isolate.py'),
        '--video', filePath,
        '--ep', ep_folder,
        '--output_dir', path.resolve('./workspace/2_isolated'),
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: 'Vocal isolation initiated.',
      tracking_id: ep_folder,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder') || '';
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

  const statusPath = path.resolve(`./jobs/${ep_folder}/status_isolate.json`);
  if (!fs.existsSync(statusPath)) return NextResponse.json({ status: 'offline' });

  const data = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));

  // Double-check stems on disk before reporting done
  if (data.status === 'done') {
    const ep_dir = path.resolve(`./workspace/2_isolated/${ep_folder}`);
    if (!fs.existsSync(path.join(ep_dir, 'vocals.wav'))) {
      return NextResponse.json({ ...data, status: 'processing', message: 'Waiting for stems' });
    }
  }

  return NextResponse.json(data);
}
