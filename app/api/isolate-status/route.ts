import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

function findDemucsWav(isolatedDir: string, filename: string): string | null {
  const htdemucsDir = path.join(isolatedDir, 'htdemucs');
  if (!fs.existsSync(htdemucsDir)) return null;
  for (const stemDir of fs.readdirSync(htdemucsDir)) {
    const candidate = path.join(htdemucsDir, stemDir, filename);
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function copyToStable(src: string, dst: string): void {
  if (!fs.existsSync(dst)) {
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst);
  }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = searchParams.get('ep_folder');

  if (!ep_folder) {
    return NextResponse.json({ error: "ep_folder query parameter is required" }, { status: 400 });
  }

  const isolatedDir = path.resolve(`./workspace/2_isolated/${ep_folder}`);

  if (!fs.existsSync(isolatedDir)) {
    return NextResponse.json({ status: "offline", message: "Isolation folder not found." });
  }

  const stableVocals = path.join(isolatedDir, 'vocals.wav');
  const stableBg = path.join(isolatedDir, 'no_vocals.wav');

  // Return immediately if stable paths already exist (idempotent on re-poll)
  if (fs.existsSync(stableVocals) && fs.existsSync(stableBg)) {
    return NextResponse.json({
      status: "done",
      vocals_path: stableVocals.replace(/\\/g, '/'),
      bg_path: stableBg.replace(/\\/g, '/'),
    });
  }

  const vocalsRaw = findDemucsWav(isolatedDir, 'vocals.wav');
  const bgRaw = findDemucsWav(isolatedDir, 'no_vocals.wav');

  if (vocalsRaw && bgRaw) {
    // First detection: copy stems to stable predictable paths
    copyToStable(vocalsRaw, stableVocals);
    copyToStable(bgRaw, stableBg);
    return NextResponse.json({
      status: "done",
      vocals_path: stableVocals.replace(/\\/g, '/'),
      bg_path: stableBg.replace(/\\/g, '/'),
    });
  }

  return NextResponse.json({ status: "processing", message: "Demucs still running." });
}
