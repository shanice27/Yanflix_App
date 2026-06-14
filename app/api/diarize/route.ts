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

async function groqRequest(apiKey: string, prompt: string, signal?: AbortSignal): Promise<any> {
  const res = await fetch(GROQ_BASE, {
    method: 'POST',
    signal,
    headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: GROQ_MODEL,
      messages: [{ role: 'user', content: prompt }],
      temperature: 0.1,
      response_format: { type: 'json_object' },
      max_tokens: 4096,
    }),
  });
  if (res.status === 429) throw new Error(`Groq 429: rate limited`);
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`Groq ${res.status}: ${txt.slice(0, 200)}`);
  }
  const data = await res.json();
  const text = data.choices?.[0]?.message?.content;
  if (!text) throw new Error('Empty response from Groq');
  return JSON.parse(stripJsonFences(text));
}

async function geminiRequest(model: string, apiKey: string, prompt: string): Promise<any> {
  const url = `${GEMINI_BASE}/${model}:generateContent`;
  const body = JSON.stringify({
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { responseMimeType: 'application/json', maxOutputTokens: 32768 },
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
        console.warn(`[diarize] 429 on ${model} — waiting ${delays[attempt]/1000}s`);
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

function deepMerge(target: any, patch: any): any {
  if (Array.isArray(target) || typeof target !== 'object') return patch;
  const out = { ...target };
  for (const [k, v] of Object.entries(patch)) {
    out[k] = (k in target && typeof target[k] === 'object' && !Array.isArray(target[k]))
      ? deepMerge(target[k], v)
      : v;
  }
  return out;
}

function atomicWrite(filePath: string, data: any) {
  const tmp = filePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmp, filePath);
}

// Load scene context for a given episode folder and show slug, if available.
// Returns a block of text ready to inject into the diarize prompt.
function loadSceneContext(ep_folder: string, showSlug: string): string {
  // Derive episode key from ep_folder (e.g. "smoking_supermarket_s01e01" → "s01e01")
  const epMatch = ep_folder.match(/(s\d{2}e\d{2})/i);
  const epKey = epMatch ? epMatch[1].toLowerCase() : null;
  if (!epKey) return '';

  const candidates = [
    path.resolve(`./characters/shows/${showSlug}/${epKey}_scene_context.json`),
    path.resolve(`./characters/shows/${showSlug}/s01e01_scene_context.json`),
  ];

  for (const p of candidates) {
    if (!fs.existsSync(p)) continue;
    try {
      const ctx = JSON.parse(fs.readFileSync(p, 'utf-8'));
      const chars = (ctx.characters || []) as any[];
      const scenes = (ctx.scenes || []) as any[];

      const rosterLines = chars.map((c: any) =>
        `  - ${c.id}${c.generic ? ' [GENERIC — use generic_female or similar]' : ''}: ${c.description}`
      ).join('\n');

      const sceneLines = scenes.map((s: any) =>
        `  ${s.timestamp} [${s.location}] speakers: ${s.speakers.join(', ')} — ${s.note}`
      ).join('\n');

      return `
STRICT CHARACTER ROSTER (human-verified by watching the episode — do NOT invent names outside this list):
${rosterLines}

SCENE MAP (timestamp → who is speaking):
${sceneLines}

IMPORTANT RULES:
- Only assign character_name values that appear in the roster above.
- "tayama" and "yamada" are the same voice actress but distinct characters — use "tayama" when she is off-duty/smoking, "yamada" when she is on the register.
- Internal monologue lines belong to the character currently shown on screen (usually "sasaki_male_lead").
- Thought-bubble lines voiced by another character still use that character's name.
- "female_passerby_generic" for the two unnamed women outside the supermarket (~8:02–8:44).
- source: ${ctx.source || 'scene_context'}`;
    } catch {
      return '';
    }
  }
  return '';
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, source_lang, show_name, force } = body;

    // --- Validate synchronously ---

    if (!ep_folder || !source_lang) {
      return NextResponse.json({ error: 'ep_folder and source_lang required' }, { status: 400 });
    }

    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) return NextResponse.json({ error: 'GEMINI_API_KEY not configured' }, { status: 500 });

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const whisperPath = path.join(jobDir, 'state_whisper.json');
    if (!fs.existsSync(whisperPath)) {
      return NextResponse.json({ error: 'state_whisper.json not found — run transcribe first' }, { status: 400 });
    }

    const segments: any[] = JSON.parse(fs.readFileSync(whisperPath, 'utf-8'));
    if (!Array.isArray(segments) || segments.length === 0) {
      return NextResponse.json({ error: 'Whisper output is empty' }, { status: 400 });
    }

    const N = segments.length;

    // Idempotency: already done (skip if force=true)
    const statusPath = path.join(jobDir, 'status_diarize.json');
    if (!force && fs.existsSync(statusPath)) {
      try {
        const s = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
        if (s.status === 'done') return NextResponse.json({ status: 'done', ep_folder, line_count: N });
        if (s.status === 'processing') return NextResponse.json({ status: 'processing', ep_folder });
      } catch {}
    }

    // Write processing status immediately so n8n poll sees it right away
    atomicWrite(statusPath, { stage: 'diarize', status: 'processing', progress: 0, updated_at: new Date().toISOString() });

    // Minimal input: strip word timestamps — only id, start, end, text needed for speaker ID
    const transcriptInput = segments.map((s: any, i: number) => ({
      i: s.id ?? i, s: Math.round(s.start * 10) / 10, e: Math.round(s.end * 10) / 10, t: s.text,
    }));

    // Derive show slug — prefer slug that actually has a scene context file for this episode
    const showsDir = path.resolve('./characters/shows');
    let showSlug = '';
    if (fs.existsSync(showsDir)) {
      const ep = ep_folder.toLowerCase();
      const epMatch = ep_folder.match(/(s\d{2}e\d{2})/i);
      const epKey = epMatch ? epMatch[1].toLowerCase() : '';
      const slugs = fs.readdirSync(showsDir).filter(d => fs.statSync(path.join(showsDir, d)).isDirectory());
      const scored = slugs.map(sl => {
        const words = sl.replace(/_/g, ' ').split(' ').filter((w: string) => w.length > 3);
        const matches = words.filter((w: string) => ep.includes(w)).length;
        const hasCtx = epKey && fs.existsSync(path.resolve(`./characters/shows/${sl}/${epKey}_scene_context.json`)) ? 1 : 0;
        return { sl, score: hasCtx * 1000 + matches };
      }).filter(x => x.score > 0).sort((a, b) => b.score - a.score);
      showSlug = scored[0]?.sl ?? '';
    }
    const sceneContext = loadSceneContext(ep_folder, showSlug);
    if (sceneContext) {
      console.log(`[diarize] Loaded scene context for ${ep_folder} (show: ${showSlug})`);
    } else {
      console.warn(`[diarize] No scene context found for ${ep_folder} — speaker names will be guessed`);
    }

    const buildPrompt = (chunk: any[], chunkN: number, totalN: number, songs: boolean) =>
`You are a casting director for an anime dubbing studio. Assign speaker names and emotions to each line.

Source language: ${source_lang} | Show: ${show_name || ep_folder}
Batch: ${chunkN} lines (of ${totalN} total in episode)
${sceneContext}

Input transcript (i=index, s=start_sec, e=end_sec, t=text):
${JSON.stringify(chunk)}

Return ONLY valid JSON with two keys: "lines" and "songs".
"lines": array of EXACTLY ${chunkN} objects, one per input line, same order:
  { "i": <same i as input>, "c": "<character_name>", "em": "<emotion>", "tp": "<speech|singing>" }
  - character_name: lowercase_underscore slug. Use same name every time same speaker appears.
  - emotion: neutral | cheerful | angry | sad | whisper | exhausted | excited | fearful
  - tp: "speech" or "singing"
${songs ? `"songs": detect any intro/outro music: [{ "segment": "intro|outro", "start": <sec>, "end": <sec> }]` : `"songs": []`}
No markdown. No extra text. Count "lines" — must equal ${chunkN}.`;

    const CHUNK_SIZE = 50;

    // Fire-and-forget: return immediately so n8n's 5-min HTTP timeout is never hit
    const groqKey = process.env.GROQ_API_KEY || '';
    const chunkCacheDir = path.join(jobDir, 'diarize_chunks');
    fs.mkdirSync(chunkCacheDir, { recursive: true });
    // force=true with explicit clear_cache wipes saved chunks (normal force re-run keeps them)
    if (force && body.clear_cache) {
      fs.readdirSync(chunkCacheDir).forEach(f => fs.unlinkSync(path.join(chunkCacheDir, f)));
      console.log('[diarize] Chunk cache cleared');
    }

    void (async () => {
      let allLines: any[] = [];
      let allSongs: any[] = [];
      let lastErr: any = null;

      try {
        // Process in 50-line chunks — each chunk is a fast, small Groq call
        const chunks: any[][] = [];
        for (let i = 0; i < transcriptInput.length; i += CHUNK_SIZE) {
          chunks.push(transcriptInput.slice(i, i + CHUNK_SIZE));
        }

        const tryChunk = async (ci: number, attempt: number): Promise<any> => {
          const chunk = chunks[ci];
          const isFirst = ci === 0;
          const prompt = buildPrompt(chunk, chunk.length, N, isFirst);
          let chunkResult: any = null;

          // 1. Groq (primary)
          if (groqKey) {
            try {
              const ctrl = new AbortController();
              const t = setTimeout(() => ctrl.abort(), 120000);
              chunkResult = await groqRequest(groqKey, prompt, ctrl.signal);
              clearTimeout(t);
              console.log(`[diarize] Chunk ${ci+1}/${chunks.length} — Groq OK`);
            } catch (e: any) {
              lastErr = e;
              console.warn(`[diarize] Chunk ${ci+1} Groq failed: ${e.message}`);
            }
          }

          // 2. Gemini fallbacks
          for (let m = 0; m < GEMINI_MODELS.length && !chunkResult; m++) {
            try {
              chunkResult = await geminiRequest(GEMINI_MODELS[m], apiKey, prompt);
              console.log(`[diarize] Chunk ${ci+1}/${chunks.length} — ${GEMINI_MODELS[m]} OK`);
            } catch (e: any) {
              lastErr = e;
              console.warn(`[diarize] Chunk ${ci+1} ${GEMINI_MODELS[m]} failed: ${e.message}`);
            }
          }

          // 3. Ollama local last resort — skip if GPU is busy
          if (!chunkResult) {
            const gpuLock = path.resolve('./jobs/gpu.lock');
            if (!fs.existsSync(gpuLock)) {
              try {
                const res = await fetch('http://localhost:11434/v1/chat/completions', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    model: 'llama3.1:8b',
                    messages: [{ role: 'user', content: prompt }],
                    temperature: 0.1,
                    stream: false,
                  }),
                });
                if (res.ok) {
                  const data = await res.json();
                  const text = data.choices?.[0]?.message?.content;
                  if (text) {
                    chunkResult = JSON.parse(stripJsonFences(text));
                    console.log(`[diarize] Chunk ${ci+1}/${chunks.length} — Ollama OK`);
                  }
                }
              } catch (e: any) {
                lastErr = e;
                console.warn(`[diarize] Chunk ${ci+1} Ollama failed: ${e.message}`);
              }
            } else {
              console.warn(`[diarize] Chunk ${ci+1} skipping Ollama — GPU locked`);
            }
          }

          // Validate line count — a wrong count means the LLM dropped/added lines
          if (chunkResult) {
            const lines = chunkResult.lines ?? chunkResult;
            if (!Array.isArray(lines) || lines.length !== chunks[ci].length) {
              console.warn(`[diarize] Chunk ${ci+1} line count mismatch: got ${lines?.length}, expected ${chunks[ci].length} — will retry`);
              lastErr = new Error(`line count mismatch: got ${lines?.length}, expected ${chunks[ci].length}`);
              chunkResult = null;
            }
          }

          // 4. All providers failed or bad output — retry after cooldown (up to 2 retries)
          if (!chunkResult && attempt < 2) {
            const waitMs = attempt === 0 ? 30000 : 120000; // 30s for bad output, 2 min for rate limits
            const isRateLimit = lastErr?.message?.includes('429');
            const reason = isRateLimit ? `rate limited` : `bad output`;
            console.warn(`[diarize] Chunk ${ci+1} ${reason} (attempt ${attempt+1}) — waiting ${waitMs/1000}s before retry`);
            atomicWrite(statusPath, {
              stage: 'diarize', status: 'processing',
              progress: Math.round((allLines.length / N) * 90),
              chunks_done: ci, chunks_total: chunks.length,
              step: `${reason} — retrying chunk ${ci+1} in ${waitMs/1000}s (attempt ${attempt+2}/3)`,
              updated_at: new Date().toISOString(),
            });
            await new Promise(r => setTimeout(r, waitMs));
            return tryChunk(ci, attempt + 1);
          }

          return chunkResult;
        };

        for (let ci = 0; ci < chunks.length; ci++) {
          const cacheFile = path.join(chunkCacheDir, `chunk_${ci}.json`);

          // Load from cache if this chunk already completed
          let chunkLines: any[] | null = null;
          let cachedSongs: any[] = [];
          if (fs.existsSync(cacheFile)) {
            try {
              const cached = JSON.parse(fs.readFileSync(cacheFile, 'utf-8'));
              chunkLines = cached.lines;
              cachedSongs = cached.songs ?? [];
              console.log(`[diarize] Chunk ${ci+1}/${chunks.length} — loaded from cache ✓`);
            } catch { /* corrupt cache — reprocess */ }
          }

          if (!chunkLines) {
            const chunkResult = await tryChunk(ci, 0);

            if (!chunkResult) {
              atomicWrite(statusPath, {
                stage: 'diarize', status: 'error',
                progress: Math.round((allLines.length / N) * 90),
                chunks_done: ci, chunks_total: chunks.length,
                error: `Chunk ${ci+1}/${chunks.length} failed after 3 attempts. Last: ${lastErr?.message}`,
                updated_at: new Date().toISOString(),
              });
              return;
            }

            chunkLines = chunkResult.lines ?? chunkResult;
            cachedSongs = chunkResult.songs ?? [];
            // Save to disk immediately so re-runs can skip this chunk
            atomicWrite(cacheFile, { lines: chunkLines, songs: cachedSongs, saved_at: new Date().toISOString() });

            // 40s pause between chunks (Groq free tier ~6000 tokens/min; each chunk ~3000 tokens)
            if (ci < chunks.length - 1) await new Promise(r => setTimeout(r, 40000));
          }

          allLines.push(...chunkLines);
          if (cachedSongs.length) allSongs.push(...cachedSongs);

          const pct = Math.round(((ci + 1) / chunks.length) * 90);
          atomicWrite(statusPath, {
            stage: 'diarize', status: 'processing', progress: pct,
            chunks_done: ci + 1, chunks_total: chunks.length, updated_at: new Date().toISOString(),
          });
          console.log(`[diarize] Chunk ${ci+1}/${chunks.length} done (${allLines.length}/${N} lines)`);
        }

        // All chunks assembled — expand and merge into state_director.json
        const directorPath = path.join(jobDir, 'state_director.json');
        const existing = fs.existsSync(directorPath)
          ? JSON.parse(fs.readFileSync(directorPath, 'utf-8'))
          : {};
        const existingByIdx = new Map((existing.lines || []).map((l: any) => [l.line_index, l]));

        const pending = { standard: 'pending', aave: 'pending' };
        const expandedLines = allLines.map((r: any, i: number) => {
          const seg = segments[i] as any;
          const prev = existingByIdx.get(r.i ?? i) || {};
          return {
            line_index:    r.i ?? i,
            type:          r.tp ?? prev.type ?? 'speech',
            character:     r.c  ?? prev.character ?? `Speaker_${i}`,
            start:         seg?.start ?? prev.start ?? 0,
            end:           seg?.end   ?? prev.end   ?? 0,
            source_text:   seg?.text  ?? prev.source_text ?? '',
            text_standard: prev.text_standard ?? '',
            text_aave:     prev.text_aave     ?? '',
            detected_emotion: r.em ?? prev.detected_emotion ?? 'neutral',
            voice_id:      prev.voice_id  ?? null,
            clip_path:     prev.clip_path ?? null,
            audio_synthesis_status: prev.audio_synthesis_status ?? { ...pending },
            audio_fit_status:       prev.audio_fit_status       ?? { ...pending },
            raw_wav:  prev.raw_wav  ?? { standard: '', aave: '' },
            fit_wav:  prev.fit_wav  ?? { standard: '', aave: '' },
            synthesis_quality: prev.synthesis_quality ?? { ...pending },
            mos_score:     prev.mos_score  ?? { standard: null, aave: null },
            error_msg:     prev.error_msg  ?? null,
          };
        });

        const expandedSongs = (allSongs.length ? allSongs : existing.songs || []).map((s: any) => ({
          segment:        s.segment ?? s.seg ?? '',
          artist:         s.artist  ?? '',
          start:          s.start   ?? s.s  ?? 0,
          end:            s.end     ?? s.e  ?? 0,
          song_source:    s.song_source ?? 'generate',
          lyrics_source:  s.lyrics_source ?? '',
          lyrics_english: s.lyrics_english ?? '',
          path_mode:      s.path_mode ?? 'A',
          dubbed_wav:     s.dubbed_wav ?? '',
          vault_wav:      s.vault_wav  ?? '',
          status:         s.status ?? 'pending',
        }));

        const newState = deepMerge(existing, {
          ep_folder,
          show_name: show_name || existing.show_name || '',
          source_lang,
          cast_locked: false,
          characters: existing.characters || {},
          songs: expandedSongs,
          lines: expandedLines,
        });

        atomicWrite(directorPath, newState);
        atomicWrite(statusPath, {
          stage: 'diarize', status: 'done', progress: 100,
          line_count: N, song_count: expandedSongs.length, updated_at: new Date().toISOString(),
        });
        console.log(`[diarize] Done: ${N} lines, ${expandedSongs.length} songs — ${ep_folder}`);

      } catch (bgErr: any) {
        console.error('[diarize] Background worker crashed:', bgErr.message);
        try {
          atomicWrite(statusPath, {
            stage: 'diarize', status: 'error', progress: 0,
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
