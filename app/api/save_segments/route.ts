import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const VALID_EMOTIONS = new Set([
  'neutral', 'cheerful', 'angry', 'sad', 'whisper', 'exhausted', 'excited', 'fearful',
]);

function atomicWrite(filePath: string, data: any) {
  const tmp = filePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmp, filePath);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, edits } = body;

    if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });
    if (!Array.isArray(edits)) return NextResponse.json({ error: 'edits array required' }, { status: 400 });

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');
    if (!fs.existsSync(statePath)) {
      return NextResponse.json({ error: 'state_director.json not found' }, { status: 400 });
    }

    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));

    // edits: Array<{ line_index, character?, detected_emotion?, is_song?, text_standard?, text_aave? }>
    const byIdx = new Map(edits.map((e: any) => [e.line_index, e]));

    for (const line of (state.lines || [])) {
      const edit = byIdx.get(line.line_index);
      if (!edit) continue;

      if (edit.character !== undefined)       line.character        = edit.character;
      if (edit.detected_emotion !== undefined && VALID_EMOTIONS.has(edit.detected_emotion)) {
        line.detected_emotion = edit.detected_emotion;
      }
      if (edit.type !== undefined)            line.type             = edit.type;
      if (edit.text_standard !== undefined)   line.text_standard    = edit.text_standard;
      if (edit.text_aave !== undefined)       line.text_aave        = edit.text_aave;
    }

    atomicWrite(statePath, state);

    return NextResponse.json({ status: 'done', ep_folder, edited: edits.length });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
