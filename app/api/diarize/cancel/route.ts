import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  const { ep_folder } = await request.json();
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

  const statusPath = path.resolve(`./jobs/${ep_folder}/status_diarize.json`);
  const tmp = statusPath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({
    stage: 'diarize', status: 'cancelled',
    updated_at: new Date().toISOString(),
  }, null, 2), 'utf-8');
  fs.renameSync(tmp, statusPath);

  return NextResponse.json({ ok: true });
}

export async function PUT(request: Request) {
  // Mark diarize as manually done
  const { ep_folder, line_count } = await request.json();
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

  const jobDir = path.resolve(`./jobs/${ep_folder}`);

  // Lock the cast
  const statePath = path.join(jobDir, 'state_director.json');
  if (fs.existsSync(statePath)) {
    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
    state.cast_locked = true;
    const tmp = statePath + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf-8');
    fs.renameSync(tmp, statePath);
  }

  const statusPath = path.join(jobDir, 'status_diarize.json');
  const tmp = statusPath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({
    stage: 'diarize', status: 'done', progress: 100,
    line_count: line_count ?? 0,
    manual: true,
    updated_at: new Date().toISOString(),
  }, null, 2), 'utf-8');
  fs.renameSync(tmp, statusPath);

  return NextResponse.json({ ok: true });
}
