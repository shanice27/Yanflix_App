import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

function deepMerge(target: any, patch: any): any {
  if (Array.isArray(patch)) return patch;
  if (typeof patch !== 'object' || patch === null) return patch;
  const out = { ...(typeof target === 'object' && target !== null ? target : {}) };
  for (const [k, v] of Object.entries(patch)) {
    out[k] = (k in out && typeof out[k] === 'object' && !Array.isArray(out[k]) && typeof v === 'object' && !Array.isArray(v))
      ? deepMerge(out[k], v)
      : v;
  }
  return out;
}

function atomicWrite(filePath: string, data: any) {
  const tmp = filePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmp, filePath);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, assignments, characters } = body;

    if (!ep_folder) {
      return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');

    if (!fs.existsSync(statePath)) {
      return NextResponse.json(
        { error: 'state_director.json not found — run Casting Director first' },
        { status: 400 }
      );
    }

    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));

    // Apply per-line speaker assignments if provided
    // assignments: Array<{ line_index: number, character: string, speaker_confidence?: number }>
    if (Array.isArray(assignments)) {
      const byIdx = new Map(assignments.map((a: any) => [a.line_index, a]));
      for (const line of (state.lines || [])) {
        const asgn = byIdx.get(line.line_index);
        if (asgn) {
          if (asgn.character !== undefined) line.character = asgn.character;
          if (asgn.speaker_confidence !== undefined) line.speaker_confidence = asgn.speaker_confidence;
          if (asgn.detected_emotion !== undefined) line.detected_emotion = asgn.detected_emotion;
        }
      }
    }

    // Merge character bank metadata if provided
    if (characters && typeof characters === 'object') {
      state.characters = deepMerge(state.characters || {}, characters);
    }

    // Lock the cast
    state.cast_locked = true;

    atomicWrite(statePath, state);

    return NextResponse.json({
      status: 'done',
      ep_folder,
      cast_locked: true,
      line_count: state.lines?.length ?? 0,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
