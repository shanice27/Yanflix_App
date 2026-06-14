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

// n8n polls this every 30 s. Pop the oldest job if one exists.
export async function GET() {
  const queue = readQueue();

  if (queue.length === 0) {
    return NextResponse.json({ has_job: 'false' });
  }

  const job = queue.shift()!;
  writeQueue(queue);

  return NextResponse.json({ has_job: 'true', ...job });
}
