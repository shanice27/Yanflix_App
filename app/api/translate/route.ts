import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const GEMINI_MODELS = ['gemini-2.0-flash', 'gemini-2.0-flash-lite'];
const GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/models';
const GROQ_BASE   = 'https://api.groq.com/openai/v1/chat/completions';
const GROQ_MODEL  = 'llama-3.3-70b-versatile';

function stripJsonFences(text: string): string {
  return text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim();
}

async function groqRequest(apiKey: string, prompt: string): Promise<any> {
  const res = await fetch(GROQ_BASE, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: GROQ_MODEL,
      messages: [{ role: 'user', content: prompt }],
      temperature: 0.1,
      response_format: { type: 'json_object' },
      max_tokens: 32768,
    }),
  });
  if (res.status === 429) throw new Error('Groq 429: rate limited');
  if (!res.ok) throw new Error(`Groq ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  const text = data.choices?.[0]?.message?.content;
  if (!text) throw new Error('Empty response from Groq');
  return JSON.parse(stripJsonFences(text));
}

async function geminiRequest(model: string, apiKey: string, prompt: string): Promise<any> {
  const url = `${GEMINI_BASE}/${model}:generateContent`;
  const body = JSON.stringify({
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { responseMimeType: 'application/json' },
  });
  const delays = [10000, 30000, 60000];
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'x-goog-api-key': apiKey, 'Content-Type': 'application/json' },
      body,
    });
    if (res.status === 429) {
      if (attempt < delays.length) {
        console.warn(`[translate] 429 rate limit on ${model} — waiting ${delays[attempt]/1000}s`);
        await new Promise(r => setTimeout(r, delays[attempt]));
        continue;
      }
      throw new Error(`Gemini ${model} 429: rate limit exceeded after retries`);
    }
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`Gemini ${model} ${res.status}: ${txt.slice(0, 200)}`);
    }
    const data = await res.json();
    const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
    if (!text) throw new Error(`Empty response from ${model}`);
    return JSON.parse(stripJsonFences(text));
  }
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
      return NextResponse.json({ error: 'state_director.json not found — run Casting Director first' }, { status: 400 });
    }

    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
    const speechLines = (state.lines || []).filter((l: any) => l.type === 'speech');
    const N = speechLines.length;

    const promptPath = path.resolve('./prompts/02_dual_translation.md');
    const systemPrompt = fs.existsSync(promptPath) ? fs.readFileSync(promptPath, 'utf-8') : '';

    const groqKey = process.env.GROQ_API_KEY || '';
    const toArray = (raw: any) => Array.isArray(raw) ? raw : (raw?.lines ?? raw?.translations ?? raw);

    const CHUNK_SIZE = 50;
    const INTER_CHUNK_DELAY = 8000; // 8s between chunks to avoid rate limits

    // Idempotency: already done
    const statusPath = path.join(jobDir, 'status_translate.json');
    if (fs.existsSync(statusPath)) {
      try {
        const s = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
        if (s.status === 'done') return NextResponse.json({ status: 'done', ep_folder, line_count: N });
        if (s.status === 'processing') return NextResponse.json({ status: 'processing', ep_folder });
      } catch {}
    }

    atomicWrite(statusPath, { stage: 'translate', status: 'processing', progress: 0, updated_at: new Date().toISOString() });

    void (async () => {
      try {
        const chunks: typeof speechLines[] = [];
        for (let i = 0; i < speechLines.length; i += CHUNK_SIZE) {
          chunks.push(speechLines.slice(i, i + CHUNK_SIZE));
        }

        let totalMerged = 0;
        let totalMissed = 0;

        for (let ci = 0; ci < chunks.length; ci++) {
          const chunk = chunks[ci];
          const chunkSize = chunk.length;
          const pct = Math.round((ci / chunks.length) * 100);

          atomicWrite(statusPath, {
            stage: 'translate', status: 'processing',
            progress: pct,
            step: `Chunk ${ci + 1}/${chunks.length} (lines ${chunk[0].line_index}–${chunk[chunk.length-1].line_index})`,
            updated_at: new Date().toISOString(),
          });

          const inputPayload = chunk.map((l: any) => ({
            line_index: l.line_index,
            source_text: l.source_text,
            detected_emotion: l.detected_emotion,
          }));

          const prompt = `${systemPrompt}

Input (${chunkSize} lines):
${JSON.stringify(inputPayload)}

Return ONLY a JSON array of exactly ${chunkSize} objects, each with:
{ "line_index": number, "text_standard": string, "text_aave": string }

No markdown. No preamble. Same length as input.`;

          let result: any[] | null = null;
          let lastErr: any = null;

          // 1. Groq
          if (groqKey) {
            try {
              result = toArray(await groqRequest(groqKey, prompt));
              console.log(`[translate] chunk ${ci+1} Groq succeeded`);
            } catch (e) {
              lastErr = e;
              console.warn(`[translate] chunk ${ci+1} Groq failed:`, (e as Error).message);
            }
          }

          // 2. Gemini fallbacks
          for (let m = 0; m < GEMINI_MODELS.length && !result; m++) {
            if (m > 0) await new Promise(r => setTimeout(r, 35000));
            try {
              result = toArray(await geminiRequest(GEMINI_MODELS[m], apiKey, prompt));
              console.log(`[translate] chunk ${ci+1} ${GEMINI_MODELS[m]} succeeded`);
            } catch (e) {
              lastErr = e;
              console.warn(`[translate] chunk ${ci+1} ${GEMINI_MODELS[m]} failed:`, (e as Error).message);
            }
          }

          // 3. Ollama
          if (!result) {
            try {
              const ollamaRes = await fetch('http://localhost:11434/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: 'llama3.1:8b', prompt, stream: false, format: 'json' }),
              });
              if (!ollamaRes.ok) throw new Error(`Ollama ${ollamaRes.status}`);
              const ollamaData = await ollamaRes.json();
              result = toArray(JSON.parse(stripJsonFences(ollamaData.response || '')));
            } catch (e) {
              lastErr = e;
              console.error(`[translate] chunk ${ci+1} Ollama failed:`, (e as Error).message);
            }
          }

          if (!result) {
            atomicWrite(statusPath, {
              stage: 'translate', status: 'error', progress: pct,
              error: `Chunk ${ci+1}/${chunks.length} failed. Last: ${lastErr?.message}`,
              updated_at: new Date().toISOString(),
            });
            return;
          }

          // Merge chunk results into state by line_index
          const byIdx = new Map(result.map((r: any) => [r.line_index, r]));
          let chunkMerged = 0;
          for (const line of state.lines) {
            const t = byIdx.get(line.line_index);
            if (t) {
              if (t.text_standard) line.text_standard = t.text_standard;
              if (t.text_aave)     line.text_aave     = t.text_aave;
              chunkMerged++;
            }
          }
          const chunkMissed = chunkSize - chunkMerged;
          totalMerged += chunkMerged;
          totalMissed += chunkMissed;

          if (chunkMissed > 0) console.warn(`[translate] chunk ${ci+1}: ${chunkMissed} lines missed`);

          // Atomic write after each chunk (crash-safe)
          atomicWrite(statePath, state);

          // Inter-chunk delay (skip after last chunk)
          if (ci < chunks.length - 1) {
            await new Promise(r => setTimeout(r, INTER_CHUNK_DELAY));
          }
        }

        atomicWrite(statusPath, {
          stage: 'translate', status: 'done', progress: 100,
          line_count: N, merged: totalMerged, missed: totalMissed,
          updated_at: new Date().toISOString(),
        });
        console.log(`[translate] Done: ${totalMerged}/${N} lines translated — ${ep_folder}`);

      } catch (bgErr: any) {
        console.error('[translate] Background worker crashed:', bgErr.message);
        try {
          atomicWrite(statusPath, {
            stage: 'translate', status: 'error', progress: 0,
            error: bgErr.message, updated_at: new Date().toISOString(),
          });
        } catch {}
      }
    })();

    return NextResponse.json({ status: 'processing', ep_folder, line_count: N });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
