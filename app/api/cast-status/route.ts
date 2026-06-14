import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');

  if (!ep_folder) {
    return NextResponse.json({ error: "ep_folder query parameter is required" }, { status: 400 });
  }

  const stateFile = path.resolve(`./jobs/${ep_folder}/state_director.json`);

  if (!fs.existsSync(stateFile)) {
    return NextResponse.json({ cast_locked: false, message: "state_director.json not found yet." });
  }

  try {
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8'));
    return NextResponse.json({ cast_locked: !!state.cast_locked });
  } catch {
    return NextResponse.json({ cast_locked: false, message: "Could not parse state_director.json." });
  }
}
