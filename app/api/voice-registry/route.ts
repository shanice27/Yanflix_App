import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const show = searchParams.get('show');
  const character = searchParams.get('character');

  if (!show || !character) {
    return NextResponse.json({ error: 'show and character are required' }, { status: 400 });
  }

  const charDir = path.resolve(`./characters/shows/${show}/${character}`);
  const profileFile = path.join(charDir, 'profile.json');
  const seedsDir = path.join(charDir, 'seeds');

  const known = fs.existsSync(charDir);
  let bank_complete = false;
  let seed_count = 0;

  if (known) {
    if (fs.existsSync(profileFile)) {
      try {
        const profile = JSON.parse(fs.readFileSync(profileFile, 'utf-8'));
        bank_complete = !!profile.bank_complete;
      } catch { /* corrupt profile — treat as incomplete */ }
    }
    if (fs.existsSync(seedsDir)) {
      seed_count = fs.readdirSync(seedsDir).filter(f => f.endsWith('.wav')).length;
    }
  }

  // ~700 ElevenLabs credits per character for a full 7-emotion bank
  const credits_needed = bank_complete ? 0 : 700;

  return NextResponse.json({
    known,
    bank_complete,
    seed_count,
    credits_needed,
    char_dir: charDir.replace(/\\/g, '/'),
  });
}
