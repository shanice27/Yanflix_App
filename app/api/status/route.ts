import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

// Stage → status file basename mapping (spec Section 7 contract)
const STAGE_FILES: Record<string, string> = {
  status_isolate:         'status_isolate.json',
  status_transcribe:      'status_transcribe.json',
  status_segment:         'status_segment.json',
  status_diarize:         'status_diarize.json',
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
  try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return null; }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const ep_folder = (searchParams.get('ep_folder') || '').trim();

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

  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  const result: Record<string, any> = { ep_folder, gpu_lock_holder };

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
