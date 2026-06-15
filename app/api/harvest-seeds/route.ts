import { NextResponse } from 'next/server';
import { exec } from 'child_process';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, show_name } = body;

    if (!ep_folder || !show_name) {
      return NextResponse.json({ error: "ep_folder and show_name are required" }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    if (!fs.existsSync(jobDir)) {
      return NextResponse.json({ error: "Job folder not found — run isolate and transcribe first" }, { status: 400 });
    }

    const scriptPath = path.resolve('./engine/character_vault/harvest_voices.py');
    const cmd = [
      `conda run -n dubbing python "${scriptPath}"`,
      `--job_dir "${jobDir}"`,
      `--show "${show_name}"`,
      `--characters_root "./characters"`,
      `--chroma_host localhost --chroma_port 8000`,
      `--top_n 5 --min_sec 2.0 --min_mos 3.0`,
    ].join(' ');

    exec(cmd, (error) => {
      if (error) console.error(`[harvest-seeds] Error: ${error.message}`);
      else console.log(`[harvest-seeds] Done for ${ep_folder}`);
    });

    return NextResponse.json({ status: "processing", tracking_id: ep_folder });

  } catch (err: any) {
    return NextResponse.json({ status: "error", error: err.message }, { status: 500 });
  }
}
