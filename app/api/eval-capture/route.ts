import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

function readJson(p: string) {
  if (!fs.existsSync(p)) return null;
  try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return null; }
}

function wavDurationS(wavPath: string): number | null {
  if (!fs.existsSync(wavPath)) return null;
  try {
    const buf = Buffer.alloc(44);
    const fd = fs.openSync(wavPath, 'r');
    fs.readSync(fd, buf, 0, 44, 0);
    fs.closeSync(fd);
    const sampleRate = buf.readUInt32LE(24);
    const byteRate   = buf.readUInt32LE(28);
    const dataSize   = fs.statSync(wavPath).size - 44;
    if (!byteRate) return null;
    return Math.round((dataSize / byteRate) * 10) / 10;
  } catch { return null; }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const ep_folder = (body.ep_folder || '').trim();
    if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

    const jobDir      = path.resolve(`./jobs/${ep_folder}`);
    const isoDir      = path.resolve(`./workspace/2_isolated/${ep_folder}`);
    const goldDir     = path.resolve(`./workspace/benchmark/gold`);
    const goldEpDir   = path.join(goldDir, ep_folder);

    if (!fs.existsSync(jobDir)) {
      return NextResponse.json({ error: 'No job folder found — run the pipeline first' }, { status: 400 });
    }

    fs.mkdirSync(goldEpDir, { recursive: true });

    // Capture state files
    const stateFiles = ['state_whisper.json', 'state_director.json', 'meta.json'];
    for (const f of stateFiles) {
      const src = path.join(jobDir, f);
      if (fs.existsSync(src)) fs.copyFileSync(src, path.join(goldEpDir, f));
    }

    // Build metrics snapshot
    const whisper  = readJson(path.join(jobDir, 'state_whisper.json'));
    const director = readJson(path.join(jobDir, 'state_director.json'));

    const vocalsWav = path.join(isoDir, 'vocals.wav');
    const bgWav     = path.join(isoDir, 'no_vocals.wav');

    const metrics = {
      captured_at: new Date().toISOString(),
      ep_folder,
      isolate: {
        vocals_exists:    fs.existsSync(vocalsWav),
        bg_exists:        fs.existsSync(bgWav),
        vocals_duration_s: wavDurationS(vocalsWav),
      },
      transcribe: {
        segment_count: Array.isArray(whisper) ? whisper.length : (whisper?.segments?.length ?? null),
        source_lang:   director?.source_lang ?? null,
      },
      director: {
        line_count:   Array.isArray(director?.lines) ? director.lines.length : null,
        characters:   director?.lines
          ? [...new Set<string>(director.lines.map((l: any) => l.character).filter(Boolean))].sort()
          : null,
        emotion_dist: (() => {
          if (!director?.lines) return null;
          const dist: Record<string, number> = {};
          for (const l of director.lines) {
            const e = l.detected_emotion || 'neutral';
            dist[e] = (dist[e] || 0) + 1;
          }
          const total = director.lines.length;
          return Object.fromEntries(Object.entries(dist).map(([k, v]) => [k, Math.round((v / total) * 100) / 100]));
        })(),
      },
    };

    fs.writeFileSync(path.join(goldEpDir, 'gold_metrics.json'), JSON.stringify(metrics, null, 2));

    // Update benchmark config
    const cfgPath = path.resolve('./workspace/benchmark/config.json');
    const cfg = fs.existsSync(cfgPath) ? readJson(cfgPath) ?? {} : {};
    cfg.gold_ep_folder  = ep_folder;
    cfg.gold_captured_at = new Date().toISOString();
    cfg.show_name       = director?.show_name ?? body.show_name ?? '';
    fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2));

    return NextResponse.json({ ok: true, gold_ep_folder: ep_folder, metrics });
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
