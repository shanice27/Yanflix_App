import { NextRequest, NextResponse } from "next/server";

const ELEVENLABS_API = "https://api.elevenlabs.io/v1";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const { text, voice_id, model_id = "eleven_multilingual_v2" } = body;

  if (!text || !voice_id) {
    return NextResponse.json({ error: "text and voice_id required" }, { status: 400 });
  }

  const apiKey = process.env.ELEVENLABS_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: "ELEVENLABS_API_KEY not set in .env.local" }, { status: 500 });
  }

  const res = await fetch(`${ELEVENLABS_API}/text-to-speech/${voice_id}`, {
    method: "POST",
    headers: {
      "xi-api-key": apiKey,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text, model_id, voice_settings: { stability: 0.5, similarity_boost: 0.75 } }),
  });

  if (!res.ok) {
    const err = await res.text();
    return NextResponse.json({ error: err }, { status: res.status });
  }

  const audio = await res.arrayBuffer();
  return new NextResponse(audio, {
    status: 200,
    headers: { "Content-Type": "audio/mpeg" },
  });
}
