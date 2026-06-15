import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, line_index, track_mode } = body;

    if (ep_folder === undefined || line_index === undefined || !track_mode) {
      return NextResponse.json({ error: 'ep_folder, line_index, and track_mode required' }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');
    if (!fs.existsSync(statePath)) {
      return NextResponse.json({ error: 'state_director.json not found' }, { status: 400 });
    }

    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json({ error: `GPU busy — held by: ${holder}` }, { status: 409 });
    }

    // Clear existing status for this line so synthesize_dub.py re-processes it
    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
    const line = (state.lines || []).find((l: any) => l.line_index === Number(line_index));
    if (!line) {
      return NextResponse.json({ error: `Line ${line_index} not found` }, { status: 404 });
    }

    line.audio_synthesis_status = { ...line.audio_synthesis_status, [track_mode]: 'pending' };
    line.audio_fit_status       = { ...line.audio_fit_status,       [track_mode]: 'pending' };
    line.synthesis_quality      = { ...line.synthesis_quality,      [track_mode]: 'pending' };
    line.raw_wav                = { ...line.raw_wav,                [track_mode]: '' };
    line.fit_wav                = { ...line.fit_wav,                [track_mode]: '' };

    const tmp = statePath + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
    fs.renameSync(tmp, statePath);


    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');

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
      message: `Re-synthesizing line ${line_index} for ${track_mode} track.`,
      line_index,
      track_mode,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
