import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import os from 'os';

const ELEVEN_BASE = 'https://api.elevenlabs.io';

const BANK_PROMPTS: Array<{
  emotion: string;
  text: string;
  stability: number;
  similarity: number;
}> = [
  {
    emotion: 'neutral',
    text: "I never thought this day would actually come, but here we are. After everything we have been through, this changes absolutely everything.",
    stability: 0.50, similarity: 0.75,
  },
  {
    emotion: 'cheerful',
    text: "Oh, this is wonderful news! Everything came together better than I ever dared to hope.",
    stability: 0.38, similarity: 0.82,
  },
  {
    emotion: 'angry',
    text: "I told you this would happen. You never listen, and now look at the mess you've made!",
    stability: 0.28, similarity: 0.88,
  },
  {
    emotion: 'sad',
    text: "I just… I don't know what to say. Some things can never really be undone, can they.",
    stability: 0.62, similarity: 0.70,
  },
  {
    emotion: 'whisper',
    text: "Don't make a sound. They're right outside — if they hear us it's over.",
    stability: 0.72, similarity: 0.72,
  },
  {
    emotion: 'exhausted',
    text: "I've been awake for three days straight. I can't keep doing this much longer.",
    stability: 0.68, similarity: 0.68,
  },
  {
    emotion: 'excited',
    text: "Did you see that? It actually worked! We did it — I can't believe we actually did it!",
    stability: 0.28, similarity: 0.85,
  },
  {
    emotion: 'fearful',
    text: "Something's wrong. I can feel it. We shouldn't be here — we need to leave right now.",
    stability: 0.32, similarity: 0.82,
  },
];

function writeStatus(jobDir: string, payload: Record<string, unknown>) {
  const p = path.join(jobDir, 'status_clone.json');
  let cur: Record<string, unknown> = {};
  if (fs.existsSync(p)) {
    try { cur = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { /* */ }
  }
  Object.assign(cur, payload, { updated_at: new Date().toISOString() });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cur, null, 2), 'utf-8');
  fs.renameSync(tmp, p);
}

function buildWav(pcm: Buffer, sampleRate: number, channels: number, bitDepth: number): Buffer {
  const dataLen = pcm.length;
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + dataLen, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * (bitDepth / 8), 28);
  header.writeUInt16LE(channels * (bitDepth / 8), 32);
  header.writeUInt16LE(bitDepth, 34);
  header.write('data', 36);
  header.writeUInt32LE(dataLen, 40);
  return Buffer.concat([header, pcm]);
}

async function runClone(ep_folder: string, show_name: string, apiKey: string) {
  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  const charactersRoot = path.resolve('./characters/shows', show_name);

  writeStatus(jobDir, { stage: 'clone', status: 'processing', progress: 0, step: 'starting', logs: [], error: null });

  const logs: string[] = [];
  const log = (msg: string) => {
    logs.push(`[${new Date().toTimeString().slice(0, 8)}] ${msg}`);
    writeStatus(jobDir, { logs: logs.slice(-200) });
    console.log(`[clone-speakers] ${msg}`);
  };

  try {
    const charDirs = fs.readdirSync(charactersRoot, { withFileTypes: true })
      .filter(d => d.isDirectory())
      .map(d => d.name);

    const results: Record<string, unknown> = {};

    for (let i = 0; i < charDirs.length; i++) {
      const charName = charDirs[i];
      const charDir = path.join(charactersRoot, charName);
      const seedsDir = path.join(charDir, 'seeds');
      const profilePath = path.join(charDir, 'profile.json');

      // Check for user cancellation before each character
      try {
        const cur = JSON.parse(fs.readFileSync(path.join(jobDir, 'status_clone.json'), 'utf-8'));
        if (cur.status === 'cancelled') {
          log('Clone cancelled by user');
          return;
        }
      } catch {}

      const pct = Math.round((i / charDirs.length) * 100);
      writeStatus(jobDir, { progress: pct, step: `${charName} (${i + 1}/${charDirs.length})` });
      log(`── ${charName}`);

      let profile: Record<string, unknown> = {};
      if (fs.existsSync(profilePath)) {
        try { profile = JSON.parse(fs.readFileSync(profilePath, 'utf-8')); } catch { /* */ }
        if (profile.elevenlabs_voice_id && profile.bank_complete) {
          log(`   skipped — already cloned`);
          results[charName] = { skipped: true, voice_id: profile.elevenlabs_voice_id };
          continue;
        }
      }

      let voiceId = profile.elevenlabs_voice_id as string | undefined;

      if (!voiceId) {
        if (!fs.existsSync(seedsDir)) {
          log(`   ERROR: no seeds dir`);
          results[charName] = { error: 'no seeds directory' };
          continue;
        }
        const seedFiles = fs.readdirSync(seedsDir).filter(f => f.endsWith('.wav')).sort().slice(0, 25);
        if (seedFiles.length === 0) {
          log(`   ERROR: no seed WAVs`);
          results[charName] = { error: 'no seed WAVs found' };
          continue;
        }

        log(`   uploading ${seedFiles.length} seed(s) to ElevenLabs`);
        const form = new FormData();
        form.append('name', `${show_name}__${charName}`);
        form.append('description', `Yanflix auto-cloned: ${show_name} — ${charName}`);
        for (const seedFile of seedFiles) {
          const buf = fs.readFileSync(path.join(seedsDir, seedFile));
          form.append('files', new Blob([buf], { type: 'audio/wav' }), seedFile);
        }

        try {
          const addRes = await fetch(`${ELEVEN_BASE}/v1/voices/add`, {
            method: 'POST',
            headers: { 'xi-api-key': apiKey },
            body: form,
          });
          if (!addRes.ok) throw new Error(`ElevenLabs /v1/voices/add ${addRes.status}: ${await addRes.text()}`);
          voiceId = ((await addRes.json()) as { voice_id: string }).voice_id;
          log(`   voice_id: ${voiceId}`);
        } catch (e: any) {
          log(`   ERROR uploading: ${e.message}`);
          results[charName] = { error: e.message };
          continue;
        }
      }

      const bankDir = path.join(charDir, 'bank');
      fs.mkdirSync(bankDir, { recursive: true });

      const existingBank = (profile.bank as Record<string, string>) || {};
      const bankMeta: Record<string, string> = { ...existingBank };
      let bankErrors = 0;

      for (const { emotion, text, stability, similarity } of BANK_PROMPTS) {
        const wavPath = path.join(bankDir, `ref_${emotion}.wav`);
        if (fs.existsSync(wavPath)) {
          bankMeta[emotion] = wavPath;
          continue;
        }

        log(`   gen ref_${emotion}.wav`);
        try {
          const ttsRes = await fetch(
            `${ELEVEN_BASE}/v1/text-to-speech/${voiceId}?output_format=pcm_44100`,
            {
              method: 'POST',
              headers: { 'xi-api-key': apiKey, 'Content-Type': 'application/json' },
              body: JSON.stringify({
                text,
                model_id: 'eleven_multilingual_v2',
                voice_settings: { stability, similarity_boost: similarity },
              }),
            }
          );
          if (!ttsRes.ok) throw new Error(`TTS ${ttsRes.status}: ${await ttsRes.text()}`);
          const buf = Buffer.from(await ttsRes.arrayBuffer());
          fs.writeFileSync(wavPath, buildWav(buf, 44100, 1, 16));
          bankMeta[emotion] = wavPath;
        } catch (e: any) {
          bankErrors++;
          log(`   ERROR ref_${emotion}: ${e.message}`);
          if (emotion === 'neutral') log(`   WARNING: neutral failed — fallback chain broken`);
        }
      }

      const firstAvailable = Object.keys(bankMeta)[0] ?? null;
      const fallbackMap: Record<string, string | null> = {};
      for (const { emotion } of BANK_PROMPTS) {
        fallbackMap[emotion] = bankMeta[emotion] ?? bankMeta['neutral'] ?? (firstAvailable ? bankMeta[firstAvailable] : null);
      }

      const bankComplete = BANK_PROMPTS.every(({ emotion }) => !!bankMeta[emotion]);

      profile.elevenlabs_voice_id = voiceId;
      profile.bank = bankMeta;
      profile.bank_fallback = fallbackMap;
      profile.bank_complete = bankComplete;
      profile.bank_errors = bankErrors;
      profile.cloned_at = new Date().toISOString();

      const tmp = path.join(os.tmpdir(), `profile_${charName}_${Date.now()}.json.tmp`);
      fs.writeFileSync(tmp, JSON.stringify(profile, null, 2), 'utf-8');
      fs.renameSync(tmp, profilePath);

      log(`   done — bank_complete: ${bankComplete}, errors: ${bankErrors}`);
      results[charName] = { voice_id: voiceId, bank_complete: bankComplete, bank_errors: bankErrors };
    }

    // Reflect voice_ids into state_director.json
    const stateFile = path.resolve(`./jobs/${ep_folder}/state_director.json`);
    if (fs.existsSync(stateFile)) {
      try {
        const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8'));
        let changed = false;
        for (const ln of (state.lines || [])) {
          const r = results[ln.character] as { voice_id?: string } | undefined;
          if (r?.voice_id && ln.voice_id !== r.voice_id) {
            ln.voice_id = r.voice_id;
            changed = true;
          }
        }
        if (changed) {
          const tmp = path.join(os.tmpdir(), `state_${Date.now()}.tmp`);
          fs.writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf-8');
          fs.renameSync(tmp, stateFile);
          log(`state_director updated with voice_ids`);
        }
      } catch (e: any) {
        log(`state_director update failed: ${e.message}`);
      }
    }

    writeStatus(jobDir, { status: 'done', progress: 100, step: 'complete', result: results });
    log(`Clone complete: ${Object.keys(results).length} characters`);

  } catch (e: any) {
    console.error(`[clone-speakers] fatal: ${e.message}`);
    writeStatus(jobDir, { status: 'error', error: e.message });
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, show_name, method } = body;

    if (!ep_folder || !show_name) {
      return NextResponse.json({ error: "ep_folder and show_name are required" }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    if (!fs.existsSync(jobDir)) {
      return NextResponse.json({ error: "Job folder not found" }, { status: 400 });
    }

    const charactersRoot = path.resolve('./characters/shows', show_name);
    if (!fs.existsSync(charactersRoot)) {
      return NextResponse.json({ error: `No characters found at ${charactersRoot} — run harvest-seeds first` }, { status: 400 });
    }

    // Use local IndexTTS2 if explicitly requested or ElevenLabs key is absent
    const apiKey = process.env.ELEVENLABS_API_KEY;
    const useLocal = method === 'local' || !apiKey;

    if (useLocal) {
      const { exec } = await import('child_process');
      const scriptPath = path.resolve('./python_backend/build_emotion_bank.py');
      const cmd = [
        `conda run -n sonitr python "${scriptPath}"`,
        `--show "${show_name}"`,
        `--characters_root "./characters"`,
        `--job_dir "${jobDir}"`,
      ].join(' ');
      exec(cmd, (error) => {
        if (error) console.error(`[clone-speakers/local] Error: ${error.message}`);
        else console.log(`[clone-speakers/local] Done for ${ep_folder}`);
      });
      return NextResponse.json({ status: "processing", ep_folder, method: "indextts2_local" });
    }

    // ElevenLabs path — fire and forget
    runClone(ep_folder, show_name, apiKey).catch(e =>
      console.error(`[clone-speakers] unhandled: ${e.message}`)
    );

    return NextResponse.json({ status: "processing", ep_folder, method: "elevenlabs" });

  } catch (err: any) {
    return NextResponse.json({ status: "error", error: err.message }, { status: 500 });
  }
}
