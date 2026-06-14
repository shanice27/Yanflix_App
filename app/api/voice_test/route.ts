import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import os from 'os';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { character, emotion = 'neutral', text, show_name } = body;

    if (!character || !text) {
      return NextResponse.json({ error: 'character and text required' }, { status: 400 });
    }

    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json({ error: `GPU busy — held by: ${holder}` }, { status: 409 });
    }

    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');
    const showSlug = (show_name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');

    // Find character dir
    let charDir = path.join(charsRoot, 'shows', showSlug, character);
    if (!fs.existsSync(charDir)) charDir = path.join(charsRoot, 'global_roster', character);
    if (!fs.existsSync(charDir)) {
      return NextResponse.json({ error: `Character '${character}' not found in vault` }, { status: 404 });
    }

    // Find ref wav
    const emotionRef = path.join(charDir, `ref_${emotion}.wav`);
    const neutralRef = path.join(charDir, 'ref_neutral.wav');
    const refWav = fs.existsSync(emotionRef) ? emotionRef
      : fs.existsSync(neutralRef) ? neutralRef
      : fs.readdirSync(charDir).filter(f => f.startsWith('ref_') && f.endsWith('.wav'))
           .map(f => path.join(charDir, f))[0];

    if (!refWav) {
      return NextResponse.json(
        { error: `No ref wav found for character '${character}' emotion '${emotion}'` },
        { status: 404 }
      );
    }

    // Use a temp file for output
    const tmpOut = path.join(os.tmpdir(), `yanflix_voicetest_${Date.now()}.wav`);

    // Run IndexTTS2 synchronously (short test sentence)
    const { execFileSync } = await import('child_process');
    try {
      execFileSync(
        'conda',
        [
          'run', '-n', 'dubbing',
          'python', '-c',
          `from indextts.infer import IndexTTS; m=IndexTTS(model_dir="model/IndexTTS2",cfg_path="model/IndexTTS2/config.yaml"); m.infer(audio_prompt="${refWav.replace(/\\/g, '\\\\')}",text=${JSON.stringify(text)},output_path="${tmpOut.replace(/\\/g, '\\\\')}")`,
        ],
        { shell: false, timeout: 60000 }
      );
    } catch (e: any) {
      return NextResponse.json({ error: `IndexTTS2 failed: ${e.message}` }, { status: 500 });
    }

    if (!fs.existsSync(tmpOut)) {
      return NextResponse.json({ error: 'IndexTTS2 produced no output' }, { status: 500 });
    }

    const audioBuffer = fs.readFileSync(tmpOut);
    fs.unlinkSync(tmpOut);

    return NextResponse.json({
      status: 'done',
      audio_base64: audioBuffer.toString('base64'),
      format: 'wav',
      character,
      emotion,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
