import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, character, track_mode } = body;

    if (!ep_folder || !character || !track_mode) {
      return NextResponse.json({ error: 'ep_folder, character, and track_mode required' }, { status: 400 });
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

    // Reset synthesis status for all lines of this character
    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
    let resetCount = 0;
    for (const line of (state.lines || [])) {
      if (line.character !== character || line.type !== 'speech') continue;
      line.audio_synthesis_status = { ...line.audio_synthesis_status, [track_mode]: 'pending' };
      line.audio_fit_status       = { ...line.audio_fit_status,       [track_mode]: 'pending' };
      line.synthesis_quality      = { ...line.synthesis_quality,      [track_mode]: 'pending' };
      line.raw_wav                = { ...line.raw_wav,                [track_mode]: '' };
      line.fit_wav                = { ...line.fit_wav,                [track_mode]: '' };
      resetCount++;
    }

    if (resetCount === 0) {
      return NextResponse.json({ status: 'done', message: `No lines found for character '${character}'` });
    }

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
      message: `Re-synthesizing ${resetCount} lines for character '${character}' (${track_mode} track).`,
      character,
      track_mode,
      reset_count: resetCount,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
