import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, character, show_name, clip_paths } = body;

    if (!ep_folder || !character) {
      return NextResponse.json({ error: 'ep_folder and character required' }, { status: 400 });
    }

    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');
    const showSlug = (show_name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
    const charDir = path.join(charsRoot, 'shows', showSlug, character);
    const seedsDir = path.join(charDir, 'seeds');
    fs.mkdirSync(seedsDir, { recursive: true });

    const copied: string[] = [];
    const skipped: string[] = [];

    if (Array.isArray(clip_paths)) {
      for (const srcPath of clip_paths) {
        if (!fs.existsSync(srcPath)) { skipped.push(srcPath); continue; }
        const dest = path.join(seedsDir, path.basename(srcPath));
        fs.copyFileSync(srcPath, dest);
        copied.push(dest);
      }
    } else {
      // Auto-harvest from line_clips if no specific paths given
      const clipsDir = path.resolve(`./jobs/${ep_folder}/line_clips`);
      if (fs.existsSync(clipsDir)) {
        // Read state_director.json to find lines for this character
        const statePath = path.resolve(`./jobs/${ep_folder}/state_director.json`);
        if (fs.existsSync(statePath)) {
          const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
          const charLines = (state.lines || []).filter(
            (l: any) => l.character === character && l.clip_path
          );
          for (const line of charLines) {
            const src = path.resolve(line.clip_path);
            if (fs.existsSync(src)) {
              const dest = path.join(seedsDir, path.basename(src));
              fs.copyFileSync(src, dest);
              copied.push(dest);
            }
          }
        }
      }
    }

    // Write minimal profile.json
    const profilePath = path.join(charDir, 'profile.json');
    const existing = fs.existsSync(profilePath)
      ? JSON.parse(fs.readFileSync(profilePath, 'utf-8'))
      : {};
    const updated = {
      ...existing,
      character,
      show: showSlug,
      seed_count: copied.length,
      bank_complete: existing.bank_complete || false,
      updated_at: new Date().toISOString(),
    };
    fs.writeFileSync(profilePath, JSON.stringify(updated, null, 2));

    return NextResponse.json({
      status: 'done',
      character,
      seeds_dir: seedsDir,
      copied: copied.length,
      skipped: skipped.length,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
