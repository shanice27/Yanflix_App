import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');

  if (!ep_folder) {
    return NextResponse.json({ error: "ep_folder query parameter is required" }, { status: 400 });
  }

  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  const statusFile = path.join(jobDir, 'status_segment.json');

  if (!fs.existsSync(jobDir)) {
    return NextResponse.json({ status: "offline", message: "Job folder not found." });
  }

  if (!fs.existsSync(statusFile)) {
    return NextResponse.json({ status: "processing", message: "Segmentation not started yet." });
  }

  try {
    const status = JSON.parse(fs.readFileSync(statusFile, 'utf-8'));
    return NextResponse.json(status);
  } catch {
    return NextResponse.json({ status: "processing", message: "Status file unreadable." });
  }
}
