import { NextResponse } from 'next/server';
import { exec } from 'child_process';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  try {
    const { ep_folder } = await request.json();
    if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    if (!fs.existsSync(jobDir)) return NextResponse.json({ error: 'Job folder not found' }, { status: 400 });

    const gpuLock = path.resolve('./jobs/gpu.lock');
    if (fs.existsSync(gpuLock)) {
      const holder = fs.readFileSync(gpuLock, 'utf-8').trim();
      return NextResponse.json({ error: `GPU locked by: ${holder}` }, { status: 409 });
    }

    const scriptPath = path.resolve('./engine/synthesis/synthesize_dub.py');
    const cmd = [
      `conda run -n sonitr python "${scriptPath}"`,
      `--job_dir "${jobDir}"`,
      `--track_mode aave`,
      `--characters_root "./characters"`,
    ].join(' ');

    exec(cmd, (error) => {
      if (error) console.error(`[synth-aave] Error: ${error.message}`);
      else console.log(`[synth-aave] Done for ${ep_folder}`);
    });

    return NextResponse.json({ status: 'processing', ep_folder, track: 'aave' });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
