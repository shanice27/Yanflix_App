import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  const body = await request.json();
  const { ep_folder, reason, payload } = body;

  if (!ep_folder || !reason) {
    return NextResponse.json({ error: 'ep_folder and reason are required' }, { status: 400 });
  }

  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  if (!fs.existsSync(jobDir)) {
    fs.mkdirSync(jobDir, { recursive: true });
  }

  const reviewFile = path.join(jobDir, 'pending_review.json');
  fs.writeFileSync(reviewFile, JSON.stringify({
    requested_at: new Date().toISOString(),
    reason,
    payload: payload || null,
  }, null, 2));

  return NextResponse.json({ requested: true, ep_folder, reason });
}
