import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

function readJson(p: string): any {
  try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return null; }
}

function sceneContextPath(showSlug: string, epFolder: string): string | null {
  const epMatch = epFolder.match(/(s\d{2}e\d{2})/i);
  if (!epMatch) return null;
  const p = path.resolve(`./characters/shows/${showSlug}/${epMatch[1].toLowerCase()}_scene_context.json`);
  return fs.existsSync(p) ? p : null;
}

function findShowSlug(epFolder: string): string {
  const showsDir = path.resolve('./characters/shows');
  if (!fs.existsSync(showsDir)) return '';
  const slugs = fs.readdirSync(showsDir).filter(d =>
    fs.statSync(path.join(showsDir, d)).isDirectory()
  );
  const ep = epFolder.toLowerCase();

  // Score each slug: prefer slugs with scene context, then by word match count
  const scored = slugs.map(sl => {
    const words = sl.replace(/_/g, ' ').split(' ').filter(w => w.length > 3);
    const matches = words.filter(w => ep.includes(w)).length;
    const hasCtx = sceneContextPath(sl, epFolder) !== null ? 1 : 0;
    return { sl, score: hasCtx * 1000 + matches };
  }).filter(x => x.score > 0).sort((a, b) => b.score - a.score);

  return scored[0]?.sl ?? '';
}

export async function GET() {
  const jobsDir = path.resolve('./jobs');
  if (!fs.existsSync(jobsDir)) return NextResponse.json([]);

  const episodes = fs.readdirSync(jobsDir)
    .filter(name => {
      const full = path.join(jobsDir, name);
      return fs.statSync(full).isDirectory() && name !== 'gpu.lock';
    })
    .map(epFolder => {
      const jobDir = path.join(jobsDir, epFolder);
      const diarize = readJson(path.join(jobDir, 'status_diarize.json'));
      const director = readJson(path.join(jobDir, 'state_director.json'));
      const showSlug = director?.show_slug ?? findShowSlug(epFolder);
      // Derive episode_id by stripping show_slug prefix from ep_folder
      const episodeId = director?.episode_id ??
        (showSlug ? epFolder.replace(showSlug + '_', '') : epFolder);
      return {
        ep_folder: epFolder,
        show_slug: showSlug,
        show_name: director?.show_name ?? showSlug,
        episode_id: episodeId,
        source_lang: director?.source_lang ?? 'ko',
        raw_file_name: director?.raw_file_name ?? `${epFolder}.mp4`,
        scene_context: showSlug ? sceneContextPath(showSlug, epFolder) !== null : false,
        diarize_status: diarize?.status ?? 'offline',
      };
    })
    .sort((a, b) => a.ep_folder.localeCompare(b.ep_folder));

  return NextResponse.json(episodes);
}
