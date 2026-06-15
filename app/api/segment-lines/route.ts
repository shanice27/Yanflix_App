import { NextResponse } from 'next/server';
import { exec } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder } = body;

    if (!ep_folder) {
      return NextResponse.json({ error: "ep_folder is required" }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const isolatedDir = path.resolve(`./workspace/2_isolated/${ep_folder}`);
    const vocalsPath = path.join(isolatedDir, 'vocals.wav');
    const instrPath = path.join(isolatedDir, 'no_vocals.wav');

    if (!fs.existsSync(vocalsPath)) {
      return NextResponse.json({ error: "vocals.wav not found — run isolate first" }, { status: 400 });
    }

    const stateDirector = path.join(jobDir, 'state_director.json');
    const stateWhisper = path.join(jobDir, 'state_whisper.json');

    // segment_lines.py needs state_director.json.
    // If it doesn't exist yet (WF2 hasn't run), bootstrap a minimal version
    // from state_whisper.json so segmentation can run immediately after transcription.
    // WF2's save-state deep-merge will enrich it later without touching clip_path.
    if (!fs.existsSync(stateDirector)) {
      if (!fs.existsSync(stateWhisper)) {
        return NextResponse.json({
          error: "Neither state_director.json nor state_whisper.json found — run transcribe first",
        }, { status: 400 });
      }
      bootstrapDirectorFromWhisper(stateWhisper, stateDirector, ep_folder);
    }

    const scriptPath = path.resolve('./engine/transcription/segment_lines.py');
    const cmd = [
      `conda run -n dubbing python "${scriptPath}"`,
      `--job_dir "${jobDir}"`,
      `--vocals "${vocalsPath}"`,
      `--instrumental "${instrPath}"`,
    ].join(' ');

    exec(cmd, (error) => {
      if (error) console.error(`[segment-lines] Error: ${error.message}`);
      else console.log(`[segment-lines] Done for ${ep_folder}`);
    });

    return NextResponse.json({ status: "processing", tracking_id: ep_folder });

  } catch (err: any) {
    return NextResponse.json({ status: "error", error: err.message }, { status: 500 });
  }
}

function bootstrapDirectorFromWhisper(whisperPath: string, directorPath: string, epFolder: string): void {
  const raw = JSON.parse(fs.readFileSync(whisperPath, 'utf-8'));

  // faster-whisper outputs a top-level array; older runners wrap it in {segments:[]}
  const segments: Array<Record<string, unknown>> = Array.isArray(raw)
    ? raw
    : (raw.segments ?? raw.lines ?? []);

  const lines = segments.map((seg: Record<string, unknown>, i: number) => ({
    line_index: i,
    type: 'speech',
    character: (seg.speaker as string) ?? `Speaker_${i}`,
    start: seg.start,
    end: seg.end,
    text_source: seg.text ?? '',
    text_standard: '',
    text_aave: '',
    detected_emotion: 'neutral',
    voice_id: null,
    clip_path: null,
  }));

  const director = {
    ep_folder: epFolder,
    bootstrapped_from: 'state_whisper.json',
    bootstrapped_at: new Date().toISOString(),
    lines,
    songs: raw.songs ?? [],
    characters: {},
  };

  const tmp = path.join(os.tmpdir(), `state_director_bootstrap_${epFolder}_${Date.now()}.json.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(director, null, 2), 'utf-8');
  fs.renameSync(tmp, directorPath);
}
