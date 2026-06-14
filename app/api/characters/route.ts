import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

function readJson(p: string): any {
  if (!fs.existsSync(p)) return null;
  try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return null; }
}

export async function GET() {
  try {
    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');

    const globalRosterDir = path.join(charsRoot, 'global_roster');
    const showsDir = path.join(charsRoot, 'shows');

    const globalRoster: any = {};
    if (fs.existsSync(globalRosterDir)) {
      for (const char of fs.readdirSync(globalRosterDir)) {
        const charPath = path.join(globalRosterDir, char);
        if (!fs.statSync(charPath).isDirectory()) continue;
        const profile = readJson(path.join(charPath, 'profile.json'));
        const seeds = fs.existsSync(path.join(charPath, 'seeds'))
          ? fs.readdirSync(path.join(charPath, 'seeds')).filter(f => f.endsWith('.wav'))
          : [];
        const refWavs = fs.readdirSync(charPath).filter(f => f.startsWith('ref_') && f.endsWith('.wav'));
        globalRoster[char] = {
          character: char,
          scope: 'global',
          bank_complete: refWavs.length >= 7,
          seed_count: seeds.length,
          ref_emotions: refWavs.map(f => f.replace('ref_', '').replace('.wav', '')),
          profile: profile || {},
        };
      }
    }

    const shows: any = {};
    if (fs.existsSync(showsDir)) {
      for (const show of fs.readdirSync(showsDir)) {
        const showPath = path.join(showsDir, show);
        if (!fs.statSync(showPath).isDirectory()) continue;
        shows[show] = {};
        for (const char of fs.readdirSync(showPath)) {
          if (char === 'songs') continue;
          const charPath = path.join(showPath, char);
          if (!fs.statSync(charPath).isDirectory()) continue;
          const profile = readJson(path.join(charPath, 'profile.json'));
          const seeds = fs.existsSync(path.join(charPath, 'seeds'))
            ? fs.readdirSync(path.join(charPath, 'seeds')).filter(f => f.endsWith('.wav'))
            : [];
          const refWavs = fs.readdirSync(charPath).filter(f => f.startsWith('ref_') && f.endsWith('.wav'));
          shows[show][char] = {
            character: char,
            show,
            bank_complete: refWavs.length >= 7,
            seed_count: seeds.length,
            ref_emotions: refWavs.map(f => f.replace('ref_', '').replace('.wav', '')),
            profile: profile || {},
          };
        }
      }
    }

    return NextResponse.json({ global_roster: globalRoster, shows });
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
