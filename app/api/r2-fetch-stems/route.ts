import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { pipeline } from "stream/promises";
import { Readable } from "stream";
import { GetObjectCommand, DeleteObjectCommand } from "@aws-sdk/client-s3";
import { r2, R2_BUCKET } from "../../../lib/r2";

const WORKSPACE_ROOT = path.resolve(process.env.WORKSPACE_ROOT ?? "./workspace");

async function streamToFile(body: any, destPath: string): Promise<void> {
  const readable = body instanceof Readable ? body : Readable.fromWeb(body as any);
  await pipeline(readable, fs.createWriteStream(destPath));
}

export async function POST(req: Request) {
  const { ep_folder } = await req.json();

  if (!ep_folder) {
    return NextResponse.json({ error: "ep_folder required" }, { status: 400 });
  }

  const vocalsKey  = `${ep_folder}/vocals.wav`;
  const instruKey  = `${ep_folder}/instrumental.wav`;
  const sourceKey  = `${ep_folder}/source_audio.wav`;
  const stableDir  = path.join(WORKSPACE_ROOT, "2_isolated", ep_folder);
  const vocalsPath = path.join(stableDir, "vocals.wav");
  const instruPath = path.join(stableDir, "instrumental.wav");

  fs.mkdirSync(stableDir, { recursive: true });

  try {
    // Download vocals
    const vocalsObj = await r2.send(new GetObjectCommand({ Bucket: R2_BUCKET, Key: vocalsKey }));
    await streamToFile(vocalsObj.Body, vocalsPath);

    // Download instrumental
    const instruObj = await r2.send(new GetObjectCommand({ Bucket: R2_BUCKET, Key: instruKey }));
    await streamToFile(instruObj.Body, instruPath);

    // Verify non-empty
    if (fs.statSync(vocalsPath).size === 0 || fs.statSync(instruPath).size === 0) {
      throw new Error("Downloaded stems are empty — RunPod output may be corrupt");
    }

    // Write status_isolate.json
    const statusDir = path.resolve(`./jobs/${ep_folder}`);
    fs.mkdirSync(statusDir, { recursive: true });
    fs.writeFileSync(
      path.join(statusDir, "status_isolate.json"),
      JSON.stringify({
        stage: "isolate",
        status: "done",
        progress: 100,
        owner: "n8n",
        vocals_path: vocalsPath,
        instrumental_path: instruPath,
        updated_at: new Date().toISOString(),
      })
    );

    return NextResponse.json({ status: "done", vocals_path: vocalsPath, instrumental_path: instruPath });

  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });

  } finally {
    // Always clean up R2 — even if download failed (prevents storage accumulation)
    for (const key of [vocalsKey, instruKey, sourceKey]) {
      try {
        await r2.send(new DeleteObjectCommand({ Bucket: R2_BUCKET, Key: key }));
      } catch {}
    }
  }
}
