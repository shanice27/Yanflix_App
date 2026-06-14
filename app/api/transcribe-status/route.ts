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
  const outputFile = path.join(jobDir, 'state_whisper.json');

  if (!fs.existsSync(jobDir)) {
    return NextResponse.json({ status: "offline", message: "Job folder not found." });
  }

  if (fs.existsSync(outputFile)) {
    return NextResponse.json({ status: "done", output: outputFile.replace(/\\/g, '/') });
  }

  return NextResponse.json({ status: "processing", message: "Whisper still running." });
}
