import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET() {
  const lockFile = path.resolve('./jobs/gpu.lock');
  const locked = fs.existsSync(lockFile);
  let holder = null;
  if (locked) {
    try { holder = fs.readFileSync(lockFile, 'utf-8').trim(); } catch { /* */ }
  }
  return NextResponse.json({ locked, holder });
}
