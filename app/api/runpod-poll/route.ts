import { NextResponse } from "next/server";

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const jobId = searchParams.get("job_id");

    if (!jobId) {
      return NextResponse.json({ error: "job_id required" }, { status: 400 });
    }

    const endpointId = process.env.RUNPOD_ENDPOINT_ID;
    const res = await fetch(`https://api.runpod.ai/v2/${endpointId}/status/${jobId}`, {
      headers: { Authorization: `Bearer ${process.env.RUNPOD_API_KEY}` },
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: `RunPod poll failed: ${text}` }, { status: 502 });
    }

    const data = await res.json();

    // Normalize RunPod statuses → done/processing/error
    // Raw RunPod statuses: IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED | CANCELLED | TIMED_OUT
    const status =
      data.status === "COMPLETED" ? "done"
      : data.status === "FAILED" || data.status === "CANCELLED" || data.status === "TIMED_OUT" ? "error"
      : "processing";

    return NextResponse.json({
      job_id: jobId,
      runpod_status: data.status, // raw, for logging
      status,                     // normalized: done|processing|error — n8n reads this
      output: data.output ?? null,
      error:  data.error  ?? null,
    });

  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
