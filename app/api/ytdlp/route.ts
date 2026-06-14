import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { url, ep_folder } = body;

    if (!url || !ep_folder) {
      return NextResponse.json({ error: 'url and ep_folder required' }, { status: 400 });
    }

    // Source videos go to 0_raw_videos/ — never 1_inputs/ (Bug 4 fix)
    const outputDir = path.resolve('./workspace/0_raw_videos');
    fs.mkdirSync(outputDir, { recursive: true });

    const outputTemplate = path.join(outputDir, `${ep_folder}.%(ext)s`);

    // CORRECT: array-form spawn — URL never goes through shell
    const worker = spawn(
      'yt-dlp',
      [
        '--no-playlist',
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        '-o', outputTemplate,
        url,
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: 'Download started.',
      output_dir: outputDir,
      ep_folder,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
