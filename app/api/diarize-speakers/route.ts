import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

function writeStatus(jobDir: string, payload: object) {
  const p = path.join(jobDir, 'status_diarize_speakers.json');
  const cur = fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf-8')) : {};
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({ ...cur, ...payload, updated_at: new Date().toISOString() }, null, 2));
  fs.renameSync(tmp, p);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, show_name } = body;
    if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

    const hfToken  = process.env.HF_TOKEN || '';
    const groqKey  = process.env.GROQ_API_KEY || '';
    if (!hfToken) return NextResponse.json({ error: 'HF_TOKEN not set' }, { status: 500 });

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');
    if (!fs.existsSync(statePath)) {
      return NextResponse.json({ error: 'state_director.json not found — run diarize first' }, { status: 400 });
    }

    // Vocals path
    const vocalsPath = path.resolve(`./workspace/2_isolated/${ep_folder}/vocals.wav`);
    if (!fs.existsSync(vocalsPath)) {
      return NextResponse.json({ error: 'vocals.wav not found' }, { status: 400 });
    }

    // GPU lock check
    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json({ error: `GPU busy: ${holder}` }, { status: 409 });
    }

    // Idempotency
    const statusPath = path.join(jobDir, 'status_diarize_speakers.json');
    if (fs.existsSync(statusPath)) {
      const s = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
      if (s.status === 'processing') return NextResponse.json({ status: 'processing' });
    }

    writeStatus(jobDir, { stage: 'diarize_speakers', status: 'processing', progress: 0 });

    const args = [
      'run', '-n', 'dubbing',
      'python', path.resolve('./python_backend/diarize_speakers.py'),
      '--ep_folder', ep_folder,
      '--vocals',    vocalsPath,
      '--job_dir',   jobDir,
      '--hf_token',  hfToken,
      '--show_name', show_name || ep_folder,
      ...(groqKey ? ['--groq_api_key', groqKey] : []),
    ];

    const worker = spawn('conda', args, { detached: true, stdio: 'ignore', shell: false });
    // Save PID so the stop endpoint can kill the process tree
    const pidPath = path.join(jobDir, 'diarize_speakers.pid');
    fs.writeFileSync(pidPath, String(worker.pid));
    worker.unref();

    return NextResponse.json({ status: 'processing', ep_folder, pid: worker.pid });
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}

export async function DELETE(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

  const jobDir  = path.resolve(`./jobs/${ep_folder}`);
  const pidPath = path.join(jobDir, 'diarize_speakers.pid');
  const statusPath = path.join(jobDir, 'status_diarize_speakers.json');

  let killed = false;
  if (fs.existsSync(pidPath)) {
    const pid = fs.readFileSync(pidPath, 'utf-8').trim();
    try {
      // /T kills the whole process tree (conda spawns children)
      const { execSync } = await import('child_process');
      execSync(`taskkill /F /T /PID ${pid}`, { stdio: 'ignore' });
      killed = true;
    } catch { /* process may already be gone */ }
    fs.unlinkSync(pidPath);
  }

  // Clear GPU lock if this job held it
  if (fs.existsSync(GPU_LOCK)) {
    const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
    if (holder.includes(ep_folder)) fs.unlinkSync(GPU_LOCK);
  }

  // Reset status so the button shows "Run" again
  if (fs.existsSync(statusPath)) fs.unlinkSync(statusPath);

  return NextResponse.json({ ok: true, killed });
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });
  const p = path.resolve(`./jobs/${ep_folder}/status_diarize_speakers.json`);
  if (!fs.existsSync(p)) return NextResponse.json({ status: 'not_started' });
  return NextResponse.json(JSON.parse(fs.readFileSync(p, 'utf-8')));
}
