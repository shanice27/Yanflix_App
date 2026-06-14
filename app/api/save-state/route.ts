import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import os from 'os';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, state } = body;

    if (!ep_folder || !state) {
      return NextResponse.json({ error: "ep_folder and state are required" }, { status: 400 });
    }

    const incoming = typeof state === 'string' ? JSON.parse(state) : state;
    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    fs.mkdirSync(jobDir, { recursive: true });

    const stateFile = path.join(jobDir, 'state_director.json');
    let current: Record<string, unknown> = {};
    if (fs.existsSync(stateFile)) {
      try {
        current = JSON.parse(fs.readFileSync(stateFile, 'utf-8'));
      } catch {
        current = {};
      }
    }

    // Deep-merge: WF2 owns lines[].text_standard, .text_aave, .detected_emotion, .voice_id
    // It must not stomp over audio_synthesis_status, fit_wav, clip_path, etc.
    const merged = mergeState(current, incoming);
    merged.ep_folder = ep_folder;
    merged.saved_at = new Date().toISOString();

    // Atomic write: temp file → rename
    const tmp = path.join(os.tmpdir(), `state_director_${ep_folder}_${Date.now()}.json.tmp`);
    fs.writeFileSync(tmp, JSON.stringify(merged, null, 2), 'utf-8');
    fs.renameSync(tmp, stateFile);

    return NextResponse.json({ status: "saved", ep_folder, lines: Array.isArray(merged.lines) ? merged.lines.length : 0 });

  } catch (err: any) {
    return NextResponse.json({ status: "error", error: err.message }, { status: 500 });
  }
}

function mergeState(current: Record<string, unknown>, incoming: Record<string, unknown>): Record<string, unknown> {
  const out = { ...current };

  // Top-level scalar fields: incoming wins
  for (const [k, v] of Object.entries(incoming)) {
    if (k === 'lines') continue;
    if (k === 'characters') {
      // Shallow merge characters map
      out['characters'] = mergeCharacters(
        (current['characters'] as Record<string, unknown>) || {},
        (v as Record<string, unknown>) || {}
      );
      continue;
    }
    out[k] = v;
  }

  // Merge lines[] by line_index
  const existingLines = Array.isArray(current['lines']) ? (current['lines'] as Record<string, unknown>[]) : [];
  const incomingLines = Array.isArray(incoming['lines']) ? (incoming['lines'] as Record<string, unknown>[]) : [];

  if (incomingLines.length === 0) {
    return out;
  }

  const byIndex = new Map<number, Record<string, unknown>>();
  for (const ln of existingLines) {
    byIndex.set(ln['line_index'] as number, { ...ln });
  }

  for (const ln of incomingLines) {
    const idx = ln['line_index'] as number;
    const existing = byIndex.get(idx) || {};
    // WF2-owned fields overwrite; all other fields (clip_path, fit_wav, etc.) are preserved
    byIndex.set(idx, mergeLine(existing, ln));
  }

  // Reconstruct lines[] sorted by line_index
  out['lines'] = Array.from(byIndex.values()).sort(
    (a, b) => (a['line_index'] as number) - (b['line_index'] as number)
  );

  return out;
}

// Fields WF2 (Gemini director) is authoritative over
const WF2_LINE_FIELDS = new Set([
  'text_standard', 'text_aave', 'detected_emotion', 'voice_id',
  'translation_note', 'script_note',
]);

function mergeLine(
  existing: Record<string, unknown>,
  incoming: Record<string, unknown>
): Record<string, unknown> {
  const out = { ...existing };
  for (const [k, v] of Object.entries(incoming)) {
    if (WF2_LINE_FIELDS.has(k) || !(k in out)) {
      out[k] = v;
    }
    // All other fields (clip_path, fit_wav, audio_synthesis_status, etc.) keep existing value
  }
  return out;
}

function mergeCharacters(
  existing: Record<string, unknown>,
  incoming: Record<string, unknown>
): Record<string, unknown> {
  const out = { ...existing };
  for (const [ch, data] of Object.entries(incoming)) {
    if (ch in out && typeof out[ch] === 'object' && typeof data === 'object') {
      out[ch] = { ...(out[ch] as object), ...(data as object) };
    } else {
      out[ch] = data;
    }
  }
  return out;
}
