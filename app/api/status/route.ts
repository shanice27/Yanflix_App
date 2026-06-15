import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

// Stage → status file basename mapping (spec Section 7 contract)
const STAGE_FILES: Record<string, string> = {
  status_isolate:         'status_isolate.json',
  status_transcribe:      'status_transcribe.json',
  status_segment:         'status_segment.json',
  status_diarize:         'status_diarize.json',
  status_cast:            'status_cast.json',
  status_harvest:         'status_harvest.json',
  status_clone:           'status_clone.json',
  status_translate:       'status_translate.json',
  status_song_intro:      'status_song_intro.json',
  status_song_outro:      'status_song_outro.json',
  status_synth_standard:  'status_synth_standard.json',
  status_synth_aave:      'status_synth_aave.json',
  status_fit_standard:    'status_fit_standard.json',
  status_fit_aave:        'status_fit_aave.json',
  status_render_standard: 'status_render_standard.json',
  status_render_aave:     'status_render_aave.json',
};

function readJson(p: string): any {
  if (!fs.existsSync(p)) return null;
  try {
    let text = fs.readFileSync(p, 'utf-8');
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    return JSON.parse(text);
  } catch { return null; }
}

function listFilesSafe(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

function statSafe(p: string): fs.Stats | null {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

function pickBestSourceFile(options: {
  dir: string;
  ep_folder: string;
  candidates: string[];
}) {
  // Prefer: exact ep_folder prefix matches, then newest mtime
  const { dir, ep_folder, candidates } = options;
  if (candidates.length === 0) return null;

  const scored = candidates
    .map((filename) => {
      const fullPath = path.join(dir, filename);
      const st = statSafe(fullPath);
      const mtimeMs = st?.mtimeMs ?? 0;
      const lower = filename.toLowerCase();
      const epLower = ep_folder.toLowerCase();

      const exactPrefix = lower.startsWith(epLower + '.');
      const exactName = lower.startsWith(epLower + '_');
      const sameStem = lower.startsWith(epLower);

      const extBonus = ['.mp4', '.mkv', '.mov', '.webm'].some((e) => lower.endsWith(e)) ? 5000 : 0;
      const nameBonus = (exactPrefix ? 4000 : 0) + (exactName ? 3000 : 0) + (sameStem ? 1000 : 0);

      return {
        filename,
        fullPath,
        score: nameBonus + extBonus + mtimeMs / 1_000_000,
        mtimeMs,
      };
    })
    .sort((a, b) => b.score - a.score);

  return scored[0] || null;
}

function discoverSourceFile(ep_folder: string): { filename?: string } {
  const VIDEO_AUDIO_EXTS = new Set([
    '.mp4',
    '.mkv',
    '.webm',
    '.mov',
    '.mp3',
    '.wav',
    '.m4a',
    '.aac',
    '.flac',
    '.ogg',
  ]);

  // Best-guess common per-episode folders.
  // Add/remove these without touching UI: UI just needs { filename }.
  const candidateDirs = [
    path.resolve(`./workspace/uploads_tmp`),
    path.resolve(`./workspace/sources/${ep_folder}`),
    path.resolve(`./workspace/1_sources/${ep_folder}`),
    path.resolve(`./workspace/0_inputs/${ep_folder}`),
    path.resolve(`./jobs/${ep_folder}`),
    path.resolve(`./workspace/${ep_folder}`),
    path.resolve(`./workspace/${ep_folder}/stage_01_harvest`),
  ];

  let best: { filename: string; fullPath?: string; score: number } | null = null;

  for (const dir of candidateDirs) {
    const files = listFilesSafe(dir);
    const mediaFiles = files.filter((f) => {
      const ext = path.extname(f).toLowerCase();
      return VIDEO_AUDIO_EXTS.has(ext);
    });
    if (mediaFiles.length === 0) continue;

    const chosen = pickBestSourceFile({ dir, ep_folder, candidates: mediaFiles });
    if (!chosen) continue;

    const chosenStat = statSafe(path.join(dir, chosen.filename));
    const mtimeMs = chosenStat?.mtimeMs ?? 0;

    // Keep best across directories (using the same scoring heuristic).
    // We approximate score from filename (prefix match) + extension + newest mtime.
    const lower = chosen.filename.toLowerCase();
    const epLower = ep_folder.toLowerCase();
    const exactPrefix = lower.startsWith(epLower + '.');
    const exactName = lower.startsWith(epLower + '_');
    const sameStem = lower.startsWith(epLower);

    const extBonus = ['.mp4', '.mkv', '.mov', '.webm'].some((e) => lower.endsWith(e)) ? 5000 : 0;
    const nameBonus = (exactPrefix ? 4000 : 0) + (exactName ? 3000 : 0) + (sameStem ? 1000 : 0);

    const score = nameBonus + extBonus + mtimeMs / 1_000_000;

    if (!best || score > best.score) {
      best = { filename: chosen.filename, fullPath: chosen.fullPath, score };
    }
  }

  return best ? { filename: best.filename } : {};
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = (searchParams.get('ep_folder') || '').trim();

  // --- Optional: auto-discover source asset for Source Media stage ---
  // Discover early so the rest of the function can simply attach result.filename
  const discovered = ep_folder ? discoverSourceFile(ep_folder) : {};

  // --- GPU lock holder ---
  const gpuLockPath = path.resolve('./jobs/gpu.lock');
  const gpu_lock_holder = fs.existsSync(gpuLockPath)
    ? fs.readFileSync(gpuLockPath, 'utf-8').trim()
    : null;

  if (!ep_folder) {
    // System health check (no ep_folder) — used by UI footer
    return NextResponse.json({
      gpu_lock_holder,
      system: {
        gemini_key:    !!(process.env.GEMINI_API_KEY),
        elevenlabs_key: !!(process.env.ELEVENLABS_API_KEY),
        ffmpeg:        true, // checked lazily; routes will fail with clear errors if missing
      },
    });
  }

  const jobBase = path.resolve(`./jobs/${ep_folder}`);
  // Status files may live directly in jobDir or in a /status/ subfolder
  const statusSubdir = path.join(jobBase, 'status');
  const jobDir = fs.existsSync(statusSubdir) ? statusSubdir : jobBase;
  const result: Record<string, any> = { ep_folder, gpu_lock_holder };

  // UI expects res.filename for auto-linking the Source Media stage
  if (discovered && (discovered as any).filename) {
    result.filename = (discovered as any).filename;
  }

  // --- Read each stage status file ---
  for (const [key, filename] of Object.entries(STAGE_FILES)) {
    const filePath = path.join(jobDir, filename);
    const data = readJson(filePath);
    result[key] = data?.status ?? 'offline';
    // Attach full stage data under a detail key for UI (optional consumption)
    if (data) result[`${key}_detail`] = data;
  }

  // --- Derived QC fields for WF3 Node 27b (spec Section 7) ---
  // fit_result_{track}_qc_flagged exposed as top-level numbers
  for (const track of ['standard', 'aave']) {
    const fitKey = `status_fit_${track}_detail`;
    const qcFlagged = (result[fitKey] as any)?.result?.qc_flagged ?? null;
    result[`fit_result_${track}_qc_flagged`] = qcFlagged;
  }

  // --- Per-stage progress/logs pass-through for UI cards ---
  // The UI can read these from the _detail keys but we also surface
  // the active stage's progress at top level for convenience
  const activeStage = gpu_lock_holder ? gpu_lock_holder.split(':')[0] : null;
  result.active_stage = activeStage;

  return NextResponse.json(result);
}
