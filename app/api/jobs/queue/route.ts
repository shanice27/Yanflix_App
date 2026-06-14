import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import os from 'os';

const QUEUE_FILE = path.resolve('./jobs/pending_queue.json');

function readQueue(): Record<string, unknown>[] {
  try {
    if (fs.existsSync(QUEUE_FILE)) {
      return JSON.parse(fs.readFileSync(QUEUE_FILE, 'utf-8'));
    }
  } catch { /* */ }
  return [];
}

function writeQueue(queue: Record<string, unknown>[]): void {
  fs.mkdirSync(path.dirname(QUEUE_FILE), { recursive: true });
  const tmp = path.join(os.tmpdir(), `pending_queue_${Date.now()}.json.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(queue, null, 2), 'utf-8');
  fs.renameSync(tmp, QUEUE_FILE);
}

// UI calls POST /api/jobs/queue to enqueue a new episode.
export async function POST(request: Request) {
  const body = await request.json();
  const { show_name, episode_id, source_lang, raw_file_name } = body;

  if (!show_name || !episode_id || !source_lang || !raw_file_name) {
    return NextResponse.json(
      { error: 'show_name, episode_id, source_lang, and raw_file_name are required' },
      { status: 400 }
    );
  }

  const queue = readQueue();
  const job = { show_name, episode_id, source_lang, raw_file_name, queued_at: new Date().toISOString() };
  queue.push(job);
  writeQueue(queue);

  return NextResponse.json({ status: 'queued', position: queue.length, job });
}

// Peek at the queue without consuming it — useful for UI status display.
export async function GET() {
  const queue = readQueue();
  return NextResponse.json({ length: queue.length, jobs: queue });
}
