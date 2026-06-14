import { NextResponse } from "next/server";

export async function POST(req: Request) {
  try {
    const { ep_folder, task, r2_key, source_lang } = await req.json();

    if (!ep_folder || !task || !r2_key) {
      return NextResponse.json({ error: "ep_folder, task, and r2_key required" }, { status: 400 });
    }

    if (process.env.RUNPOD_ENABLED !== "true") {
      return NextResponse.json({ error: "RUNPOD_ENABLED is not true" }, { status: 503 });
    }

    const endpointId = process.env.RUNPOD_ENDPOINT_ID;
    if (!endpointId) {
      return NextResponse.json({ error: "RUNPOD_ENDPOINT_ID not set in .env.local" }, { status: 503 });
    }

    // Use /run (async) not /runsync — Demucs takes 3-5 min, runsync has 90s timeout
    const res = await fetch(`https://api.runpod.ai/v2/${endpointId}/run`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.RUNPOD_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        input: { task, r2_key, ep_folder, source_lang },
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: `RunPod submit failed: ${text}` }, { status: 502 });
    }

    const data = await res.json();
    // RunPod /run returns { id: "job-abc", status: "IN_QUEUE" }
    return NextResponse.json({ job_id: data.id, status: data.status });

  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
