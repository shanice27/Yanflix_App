import { NextResponse } from 'next/server';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const GPU_LOCK = path.resolve('./jobs/gpu.lock');

function writeStatus(ep_folder: string, segment: string, payload: object) {
  const jobDir = path.resolve(`./jobs/${ep_folder}`);
  fs.mkdirSync(jobDir, { recursive: true });
  const p = path.join(jobDir, `status_song_${segment}.json`);
  let cur: any = {};
  if (fs.existsSync(p)) { try { cur = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch {} }
  Object.assign(cur, payload, { updated_at: new Date().toISOString() });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cur, null, 2));
  fs.renameSync(tmp, p);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { ep_folder, segment, path_mode = 'A', show_name, is_series = false } = body;

    if (!ep_folder || !segment) {
      return NextResponse.json({ error: 'ep_folder and segment required' }, { status: 400 });
    }

    const jobDir = path.resolve(`./jobs/${ep_folder}`);
    const statePath = path.join(jobDir, 'state_director.json');
    if (!fs.existsSync(statePath)) {
      return NextResponse.json({ error: 'state_director.json not found' }, { status: 400 });
    }

    const state = JSON.parse(fs.readFileSync(statePath, 'utf-8'));
    const songEntry = (state.songs || []).find((s: any) => s.segment === segment);

    if (!songEntry) {
      return NextResponse.json({ error: `Song segment '${segment}' not found` }, { status: 404 });
    }

    // Check cache first (Mode 1 — series recurring songs)
    if (songEntry.song_source === 'cache') {
      const showSlug = (show_name || state.show_name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
      const vaultDir = path.resolve(`./characters/shows/${showSlug}/songs`);
      const vaultStd = path.join(vaultDir, `${segment}_standard.wav`);
      const vaultAave = path.join(vaultDir, `${segment}_aave.wav`);

      if (fs.existsSync(vaultStd) || fs.existsSync(vaultAave)) {
        writeStatus(ep_folder, segment, {
          stage: `song_${segment}`, status: 'done', source: 'vault',
          vault_path: fs.existsSync(vaultStd) ? vaultStd : vaultAave,
        });
        return NextResponse.json({
          status: 'done', source: 'vault',
          path: fs.existsSync(vaultStd) ? vaultStd : vaultAave,
        });
      }
    }

    // Mode 2: generate — GPU job
    if (fs.existsSync(GPU_LOCK)) {
      const holder = fs.readFileSync(GPU_LOCK, 'utf-8').trim();
      return NextResponse.json({ error: `GPU busy — held by: ${holder}` }, { status: 409 });
    }

    writeStatus(ep_folder, segment, {
      stage: `song_${segment}`, status: 'processing', progress: 0,
      error: null, owner: body.owner || 'ui',
    });

    const charsRoot = path.resolve(process.env.CHARACTERS_ROOT || './characters');
    const showSlug = (show_name || state.show_name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');

    const worker = spawn(
      'conda',
      [
        'run', '-n', 'dubbing',
        'python', path.resolve('./python_backend/dub_song.py'),
        '--job_dir', jobDir,
        '--segment', segment,
        '--path_mode', path_mode,
        '--show', showSlug,
        '--characters_root', charsRoot,
        '--is_series', String(is_series),
      ],
      { detached: true, stdio: 'ignore', shell: false }
    );
    worker.unref();

    return NextResponse.json({
      status: 'processing',
      message: `Song dubbing initiated for segment '${segment}'.`,
      tracking_id: ep_folder,
      segment,
    });

  } catch (err: any) {
    return NextResponse.json({ status: 'error', error: err.message }, { status: 500 });
  }
}
