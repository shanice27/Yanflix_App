import { NextResponse } from 'next/server';
import { exec } from 'child_process';
import path from 'path';
import fs from 'fs';
import util from 'util';

const execPromise = util.promisify(exec);

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder } = body;

    if (!ep_folder) {
      return NextResponse.json({ error: "ep_folder is required" }, { status: 400 });
    }

    const bgTrack = path.resolve(`./workspace/2_isolated/${ep_folder}/htdemucs/no_vocals.wav`);
    const ttsDir = path.resolve(`./jobs/${ep_folder}/tts_audio`);
    const outputDir = path.resolve('./workspace/5_outputs');
    const outputPath = path.join(outputDir, `${ep_folder}_dubbed.mp4`);

    fs.mkdirSync(outputDir, { recursive: true });

    // Build input list of all fitted TTS wav files sorted by line index
    const ttsFiles = fs.existsSync(ttsDir)
      ? fs.readdirSync(ttsDir).filter(f => f.endsWith('.wav')).sort()
      : [];

    if (ttsFiles.length === 0) {
      return NextResponse.json({ error: "No TTS audio files found in tts_audio directory." }, { status: 400 });
    }

    // Mix all TTS tracks + instrumental stem into output mp4
    const inputs = [`-i "${bgTrack}"`, ...ttsFiles.map(f => `-i "${path.join(ttsDir, f)}"`)];
    const mixLabels = ttsFiles.map((_, i) => `[${i + 1}:a]`).join('');
    const filterComplex = `[0:a]volume=0.85[bg];${mixLabels}amix=inputs=${ttsFiles.length}[dub];[bg][dub]amix=inputs=2[out]`;
    const cmd = `ffmpeg -y ${inputs.join(' ')} -filter_complex "${filterComplex}" -map "[out]" "${outputPath}"`;

    const { stdout, stderr } = await execPromise(cmd);

    return NextResponse.json({ status: "done", output_path: outputPath, logs: [stdout, stderr] });

  } catch (err: any) {
    return NextResponse.json({ status: "error", error: err.message }, { status: 500 });
  }
}
