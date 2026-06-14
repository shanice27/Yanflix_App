import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const GEMINI_MODELS = ['gemini-2.0-flash', 'gemini-1.5-flash'];
const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/models';
const VALID_EMOTIONS = new Set([
  'neutral', 'cheerful', 'angry', 'sad', 'whisper', 'exhausted', 'excited', 'fearful',
]);

async function geminiRequest(model: string, apiKey: string, prompt: string): Promise<any> {
  const url = `${GEMINI_BASE}/${model}:generateContent`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'x-goog-api-key': apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { responseMimeType: 'application/json' },
    }),
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`Gemini ${model} ${res.status}: ${txt.slice(0, 200)}`);
  }
  const data = await res.json();
  const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!text) throw new Error(`Empty response from ${model}`);
  return JSON.parse(text);
}

function atomicWrite(filePath: string, data: any) {
  const tmp = filePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmp, filePath);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder } = body;
    if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) return NextResponse.json({ error: 'GEMINI_API_KEY not configured' }, { status: 500 });

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');
    if (!fs.existsSync(statePath)) {
      return NextResponse.json({ error: 'state_director.json not found' }, { status: 400 });
    }

    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
    const speechLines = (state.lines || []).filter((l: any) => l.type === 'speech');
    const N = speechLines.length;

    const inputPayload = speechLines.map((l: any) => ({
      line_index: l.line_index,
      character: l.character,
      source_text: l.source_text,
      text_standard: l.text_standard || '',
      current_emotion: l.detected_emotion,
    }));

    const prompt = `You are an emotion director for anime dubbing. Re-evaluate the detected_emotion for each line.
Emotion enum (ONLY use these exact strings): neutral | cheerful | angry | sad | whisper | exhausted | excited | fearful
When uncertain, use "neutral".
Do NOT modify text_standard or text_aave — only return emotion corrections.

Input (${N} lines):
${JSON.stringify(inputPayload)}

Return ONLY a JSON array of exactly ${N} objects:
{ "line_index": number, "detected_emotion": string }

No markdown. No extra keys. Same length as input.`;

    let result: any[] | null = null;
    let lastErr: any = null;
    for (let m = 0; m < GEMINI_MODELS.length; m++) {
      if (m > 0) await new Promise(r => setTimeout(r, 35000));
      try {
        const raw = await geminiRequest(GEMINI_MODELS[m], apiKey, prompt);
        result = Array.isArray(raw) ? raw : raw.lines ?? raw;
        break;
      } catch (e) { lastErr = e; }
    }

    if (!result) {
      return NextResponse.json({ error: `Gemini failed: ${lastErr?.message}` }, { status: 502 });
    }
    if (result.length !== N) {
      return NextResponse.json(
        { error: `Line count mismatch: got ${result.length}, expected ${N}` },
        { status: 422 }
      );
    }

    // Merge ONLY emotion fields — never touch text, clip_path, or synthesis status
    const byIdx = new Map(result.map((r: any) => [r.line_index, r]));
    for (const line of state.lines) {
      const d = byIdx.get(line.line_index);
      if (d?.detected_emotion && VALID_EMOTIONS.has(d.detected_emotion)) {
        line.detected_emotion = d.detected_emotion;
      }
    }

    atomicWrite(statePath, state);

    const statusPath = path.join(jobDir, 'status_direct.json');
    atomicWrite(statusPath, {
      stage: 'direct', status: 'done', progress: 100,
      line_count: N, updated_at: new Date().toISOString(),
    });

    return NextResponse.json({ status: 'done', ep_folder, line_count: N });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
