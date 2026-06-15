import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function POST(request: Request) {
  try {
    const form = await request.formData();
    const file = form.get('file') as File | null;
    if (!file) return NextResponse.json({ error: 'file required' }, { status: 400 });

    const inputsDir = path.resolve('./workspace/uploads_tmp');
    fs.mkdirSync(inputsDir, { recursive: true });

    const dest = path.join(inputsDir, file.name);
    const buf = Buffer.from(await file.arrayBuffer());
    fs.writeFileSync(dest, buf);

    return NextResponse.json({ ok: true, path: `workspace/uploads_tmp/${file.name}`, size: buf.length });
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
