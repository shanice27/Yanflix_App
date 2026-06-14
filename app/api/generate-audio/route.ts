import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { line_index, voice_id, text, emotion_tag, ep_folder } = body;

    if (!voice_id || !text || !ep_folder) {
      return NextResponse.json({ error: "voice_id, text, and ep_folder are required" }, { status: 400 });
    }

    const apiKey = process.env.ELEVENLABS_API_KEY;
    if (!apiKey) {
      return NextResponse.json({ error: "ELEVENLABS_API_KEY not configured" }, { status: 500 });
    }

    const formattedText = emotion_tag ? `[${emotion_tag}] ${text}` : text;

    const response = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${voice_id}`, {
      method: 'POST',
      headers: { 'xi-api-key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: formattedText,
        model_id: 'eleven_multilingual_v2',
        voice_settings: { stability: 0.5, similarity_boost: 0.8, style: 0.3 },
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      return NextResponse.json({ status: "error", error: err }, { status: response.status });
    }

    const audioBuffer = Buffer.from(await response.arrayBuffer());
    const outputDir = path.resolve(`./jobs/${ep_folder}/tts_audio`);
    fs.mkdirSync(outputDir, { recursive: true });
    const outputPath = path.join(outputDir, `raw_line_${line_index}.wav`);
    fs.writeFileSync(outputPath, audioBuffer);

    return NextResponse.json({ status: "done", cached_wav_path: outputPath, line_index });

  } catch (err: any) {
    return NextResponse.json({ status: "error", error: err.message }, { status: 500 });
  }
}
