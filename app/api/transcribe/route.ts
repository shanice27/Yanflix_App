import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

function writeStatus(ep_folder: string, payload: object) {
  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  fs.mkdirSync(jobDir, { recursive: true });
  const p = path.join(jobDir, 'status_transcribe.json');
  let cur: any = {};
  if (fs.existsSync(p)) { try { cur = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch {} }
  Object.assign(cur, payload, { updated_at: new Date().toISOString() });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cur, null, 2));
  fs.renameSync(tmp, p);
}

function findVocals(ep_folder: string): string | null {
  // Stable path (set by isolate.py after copying stems)
  const stable = path.resolve(`./workspace/2_isolated/${ep_folder}/vocals.wav`);
  if (fs.existsSync(stable)) return stable;

  // Fallback: scan htdemucs nested path
  const htdemucs = path.resolve(`./workspace/2_isolated/${ep_folder}/htdemucs`);
  if (!fs.existsSync(htdemucs)) return null;
  for (const subdir of fs.readdirSync(htdemucs)) {
    const candidate = path.join(htdemucs, subdir, 'vocals.wav');
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, source_lang } = body;

    if (!ep_folder || !source_lang) {
      return NextResponse.json({ error: 'ep_folder and source_lang are required' }, { status: 400 });
    }

    const vocalsPath = findVocals(ep_folder);
    if (!vocalsPath) {
      return NextResponse.json(
        { error: 'vocals.wav not found — run isolate first' },
        { status: 400 }
      );
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    if (!fs.existsSync(jobDir)) {
      return NextResponse.json({ error: 'Job folder not found — run isolate first' }, { status: 400 });
    }

    const outputPath = path.join(jobDir, 'state_whisper.json');

    // Idempotency: already transcribed
    if (fs.existsSync(outputPath)) {
      try {
        const existing = JSON.parse(fs.readFileSync(outputPath, 'utf-8'));
        if (Array.isArray(existing) && existing.length > 0) {
          writeStatus(ep_folder, { stage: 'transcribe', status: 'done', progress: 100, error: null });
          return NextResponse.json({ status: 'done', message: 'Already transcribed.', tracking_id: ep_folder });
        }
      } catch {}
    }

    // GPU lock check
    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json({ error: `GPU busy — held by: ${holder}` }, { status: 409 });
    }

    writeStatus(ep_folder, {
      stage: 'transcribe', status: 'processing', progress: 0,
      error: null, logs: [], owner: body.owner || 'ui',
    });

    const hfToken   = process.env.HF_TOKEN    || '';
    const groqKey   = process.env.GROQ_API_KEY || '';

    // CORRECT: array-form spawn — never shell=True
    const worker = spawn(
      'conda',
      [
        'run', '-n', 'dubbing',
        'python', path.resolve('./engine/transcription/transcribe.py'),
        '--vocals', vocalsPath,
        '--job_dir', jobDir,
        '--source_lang', source_lang,
        '--output', outputPath,
        ...(hfToken ? ['--hf_token', hfToken] : []),
        ...(groqKey ? ['--groq_api_key', groqKey] : []),
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: 'Whisper transcription initiated.',
      tracking_id: ep_folder,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
