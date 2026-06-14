import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import os from "os";
import { spawn } from "child_process";
import { PutObjectCommand } from "@aws-sdk/client-s3";
import { r2, R2_BUCKET } from "../../../lib/r2";

const WORKSPACE_ROOT = path.resolve(process.env.WORKSPACE_ROOT ?? "./workspace");

function spawnAsync(cmd: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { shell: false });
    proc.on("close", code => (code === 0 ? resolve() : reject(new Error(`${cmd} exited with code ${code}`))));
    proc.on("error", reject);
  });
}

export async function POST(req: Request) {
  let tmpWav: string | null = null;
  try {
    const { ep_folder, raw_file_name } = await req.json();

    if (!ep_folder || !raw_file_name) {
      return NextResponse.json({ error: "ep_folder and raw_file_name required" }, { status: 400 });
    }

    const videoPath = path.join(WORKSPACE_ROOT, "0_raw_videos", raw_file_name);
    if (!fs.existsSync(videoPath)) {
      return NextResponse.json(
        { error: `Source file not found: ${raw_file_name} — place it in workspace/0_raw_videos/` },
        { status: 404 }
      );
    }

    tmpWav = path.join(os.tmpdir(), `${ep_folder}_source_${Date.now()}.wav`);

    // Extract audio — array form, no shell=true (Bug 3 fix from master spec)
    await spawnAsync("ffmpeg", [
      "-y", "-i", videoPath,
      "-vn",
      "-acodec", "pcm_s16le",
      "-ar", "44100",
      "-ac", "2",
      tmpWav,
    ]);

    const r2Key = `${ep_folder}/source_audio.wav`;
    await r2.send(new PutObjectCommand({
      Bucket: R2_BUCKET,
      Key: r2Key,
      Body: fs.createReadStream(tmpWav),
      ContentType: "audio/wav",
    }));

    return NextResponse.json({ r2_key: r2Key, status: "uploaded" });

  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  } finally {
    if (tmpWav && fs.existsSync(tmpWav)) {
      try { fs.unlinkSync(tmpWav); } catch {}
    }
  }
}
