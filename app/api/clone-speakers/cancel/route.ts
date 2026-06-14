import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  const { ep_folder } = await request.json();
  if (!ep_folder) return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });

  const statusPath = path.resolve(`./jobs/${ep_folder}/status_clone.json`);
  let cur: Record<string, unknown> = {};
  if (fs.existsSync(statusPath)) {
    try { cur = JSON.parse(fs.readFileSync(statusPath, 'utf-8')); } catch {}
  }
  const tmp = statusPath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({
    ...cur,
    stage: 'clone',
    status: 'cancelled',
    updated_at: new Date().toISOString(),
  }, null, 2), 'utf-8');
  fs.renameSync(tmp, statusPath);

  return NextResponse.json({ ok: true });
}
