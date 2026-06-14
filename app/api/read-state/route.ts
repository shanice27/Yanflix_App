import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');
  const file = searchParams.get('file') || 'state_director.json';

  if (!ep_folder) {
    return NextResponse.json({ error: 'ep_folder required' }, { status: 400 });
  }

  // Restrict to known safe filenames to prevent path traversal
  const allowed = ['state_director.json', 'state_whisper.json', 'pending_review.json'];
  if (!allowed.includes(file)) {
    return NextResponse.json({ error: `file must be one of: ${allowed.join(', ')}` }, { status: 400 });
  }

  const stateFile = path.resolve(`./jobs/${ep_folder}/${file}`);
  if (!fs.existsSync(stateFile)) {
    return NextResponse.json({ error: `${file} not found for ep_folder: ${ep_folder}` }, { status: 404 });
  }

  const data = JSON.parse(fs.readFileSync(stateFile, 'utf-8'));
  return NextResponse.json(data);
}
