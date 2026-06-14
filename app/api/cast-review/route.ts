import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import os from 'os';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

  const statePath = path.resolve(`./jobs/${ep_folder}/state_director.json`);
  if (!fs.existsSync(statePath)) return NextResponse.json({ error: 'state_director.json not found' }, { status: 404 });

  const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
  const lines: any[] = state.lines || [];

  // Group by character — include all lines for inspection
  const groups: Record<string, { line_count: number; samples: string[]; lines: any[] }> = {};
  for (const l of lines) {
    const c = l.character || 'unknown';
    if (!groups[c]) groups[c] = { line_count: 0, samples: [], lines: [] };
    groups[c].line_count++;
    if (groups[c].samples.length < 3) groups[c].samples.push(l.source_text || '');
    groups[c].lines.push({
      line_index: l.line_index,
      character: c,
      start: l.start,
      end: l.end,
      source_text: l.source_text || '',
      text_standard: l.text_standard || '',
      text_aave: l.text_aave || '',
      detected_emotion: l.detected_emotion || 'neutral',
      type: l.type || 'speech',
    });
  }

  const characters = Object.entries(groups)
    .sort((a, b) => b[1].line_count - a[1].line_count)
    .map(([name, data]) => ({ name, ...data }));

  return NextResponse.json({
    ep_folder,
    cast_locked: !!state.cast_locked,
    total_lines: lines.length,
    show_name: state.show_name || '',
    source_lang: state.source_lang || '',
    characters,
  });
}

// Reassign individual lines: { ep_folder, line_updates: [{line_index, character}] }
export async function PATCH(request: Request) {
  const body = await request.json();
  const { ep_folder, line_updates } = body as { ep_folder: string; line_updates: { line_index: number; character: string }[] };
  if (!ep_folder || !line_updates?.length) return NextResponse.json({ error: 'ep_folder and line_updates required' }, { status: 400 });

  const statePath = path.resolve(`./jobs/${ep_folder}/state_director.json`);
  if (!fs.existsSync(statePath)) return NextResponse.json({ error: 'state_director.json not found' }, { status: 404 });

  const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
  const updateMap = new Map(line_updates.map(u => [u.line_index, u.character]));
  let changed = 0;
  for (const l of (state.lines || [])) {
    const newChar = updateMap.get(l.line_index);
    if (newChar !== undefined && newChar !== l.character) {
      l.character = newChar;
      changed++;
    }
  }

  const tmp = statePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf-8');
  fs.renameSync(tmp, statePath);

  return NextResponse.json({ ok: true, lines_updated: changed });
}

// Apply renames: { "old_name": "new_name" } — merges duplicates
export async function POST(request: Request) {
  const body = await request.json();
  const { ep_folder, renames } = body as { ep_folder: string; renames: Record<string, string> };
  if (!ep_folder || !renames) return NextResponse.json({ error: 'ep_folder and renames required' }, { status: 400 });

  const statePath = path.resolve(`./jobs/${ep_folder}/state_director.json`);
  if (!fs.existsSync(statePath)) return NextResponse.json({ error: 'state_director.json not found' }, { status: 404 });

  const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
  let changed = 0;
  for (const l of (state.lines || [])) {
    const newName = renames[l.character];
    if (newName && newName !== l.character) {
      l.character = newName;
      changed++;
    }
  }

  const tmp = statePath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf-8');
  fs.renameSync(tmp, statePath);

  return NextResponse.json({ ok: true, lines_updated: changed });
}
