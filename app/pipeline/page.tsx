"use client";
import { useState, useEffect, useRef, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";

const STAGE_META = [
  { key: "status_harvest",  label: "Harvest Seeds",  desc: "Extracts best voice clips per character from the episode audio.",        api: "/api/harvest-seeds",    color: "#d97706", extra: {} },
  { key: "status_clone",    label: "Clone Voices",   desc: "Builds 8-emotion reference bank via IndexTTS2 (local GPU).",           api: "/api/clone-speakers",   cancelApi: "/api/clone-speakers/cancel", color: "#7c3aed", extra: { method: "local" } },
  { key: "status_translate",label: "Translate",      desc: "Translates all lines to English (Standard + AAVE) via Groq/Gemini.",     api: "/api/translate",        color: "#2563eb", extra: {} },
  { key: "status_synth_standard", label: "Synth (Std)",  desc: "Synthesizes Standard English dub with IndexTTS2.",                   api: "/api/synth-standard",   color: "#16a34a", extra: {} },
  { key: "status_synth_aave",     label: "Synth (AAVE)", desc: "Synthesizes AAVE dub with IndexTTS2.",                               api: "/api/synth-aave",       color: "#15803d", extra: {} },
  { key: "status_render_standard","label": "Render (Std)", desc: "Mixes dubbed audio with BGM and renders final video.",             api: "/api/render",           color: "#0891b2", extra: { track_mode: "standard" } },
  { key: "status_render_aave",    "label": "Render (AAVE)", desc: "Mixes AAVE dub with BGM and renders final video.",               api: "/api/render",           color: "#0e7490", extra: { track_mode: "aave" } },
];

function badge(status: string) {
  if (status === "done")       return { bg: "#14532d", color: "#86efac", dot: "#22c55e", text: "DONE" };
  if (status === "processing") return { bg: "#451a03", color: "#fcd34d", dot: "#f59e0b", text: "RUNNING" };
  if (status === "error")      return { bg: "#450a0a", color: "#fca5a5", dot: "#ef4444", text: "ERROR" };
  if (status === "cancelled")  return { bg: "#1c1917", color: "#78716c", dot: "#57534e", text: "CANCELLED" };
  return { bg: "#0f172a", color: "#4b5563", dot: "#374151", text: "—" };
}

type Episode = {
  ep_folder: string;
  show_slug: string;
  show_name: string;
  episode_id: string;
  source_lang: string;
  raw_file_name: string;
  scene_context: boolean;
};

function PipelineInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [ep, setEp]             = useState(searchParams.get("ep") || "");
  const [pipeStatus, setPipe]   = useState<Record<string, any>>({});
  const [busy, setBusy]         = useState<Record<string, boolean>>({});
  const [toast, setToast]       = useState("");
  const [n8nStatus, setN8n]     = useState<"unknown"|"online"|"offline">("unknown");
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  // ---------- episode list ----------
  useEffect(() => {
    fetch("/api/episodes").then(r => r.json()).then((data: Episode[]) => {
      setEpisodes(data);
      if (!ep && data.length > 0) setEp(data[0].ep_folder);
    }).catch(() => {});

    fetch("http://localhost:5678/healthz").then(() => setN8n("online")).catch(() => setN8n("offline"));
  }, []);

  const currentEp = episodes.find(e => e.ep_folder === ep);

  // ---------- status polling ----------
  const fetchStatus = () => {
    if (!ep) return;
    fetch(`/api/status?ep_folder=${ep}`).then(r => r.json()).then(d => setPipe(d)).catch(() => {});
  };

  useEffect(() => {
    if (!ep) return;
    setPipe({});
    fetchStatus();
    router.replace(`/pipeline?ep=${encodeURIComponent(ep)}`, { scroll: false });
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(fetchStatus, 8000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [ep]);

  // ---------- trigger ----------
  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 4000); };

  const cancelStage = async (stage: typeof STAGE_META[0]) => {
    if (!stage.cancelApi || !ep) return;
    try {
      await fetch(stage.cancelApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ep_folder: ep }),
      });
      showToast("✓ Cancel requested — stopping after current character");
      fetchStatus();
    } catch (e: any) {
      showToast("Cancel failed: " + e.message);
    }
  };

  const trigger = async (stage: typeof STAGE_META[0]) => {
    if (!stage.api) return showToast("This stage runs via n8n autopilot or a Python worker directly.");
    setBusy(b => ({ ...b, [stage.key]: true }));
    try {
      const res = await fetch(stage.api, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ep_folder: ep, show_name: currentEp?.show_slug ?? ep, ...stage.extra }),
      });
      const data = await res.json();
      if (data.error) showToast("Error: " + data.error);
      else showToast(`✓ ${stage.label} started`);
      setTimeout(fetchStatus, 2000);
      // Fast poll while running
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(fetchStatus, 5000);
    } catch (e: any) {
      showToast("Error: " + e.message);
    }
    setBusy(b => ({ ...b, [stage.key]: false }));
  };

  const triggerN8nAutopilot = async () => {
    if (!currentEp) return showToast("No episode selected");
    try {
      const res = await fetch("http://localhost:5678/webhook/runpod-isolate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          show_name:    currentEp.show_name,
          show_slug:    currentEp.show_slug,
          episode_id:   currentEp.episode_id,
          ep_folder:    currentEp.ep_folder,
          source_lang:  currentEp.source_lang,
          raw_file_name: currentEp.raw_file_name,
        }),
      });
      if (res.ok) showToast("✓ WF0 triggered — Demucs running on RunPod → full pipeline");
      else showToast("n8n webhook error: " + res.status);
    } catch {
      showToast("Could not reach n8n — is Docker running?");
    }
  };

  // ---------- render ----------
  return (
    <div style={{ fontFamily: "'Inter', system-ui, sans-serif", background: "#050505", minHeight: "100vh", color: "#e5e7eb" }}>

      {toast && (
        <div style={{ position: "fixed", top: 20, right: 20, zIndex: 999, background: toast.startsWith("✓") ? "#14532d" : "#7f1d1d", color: "#fff", padding: "12px 20px", borderRadius: 10, fontSize: 14, fontWeight: 500, boxShadow: "0 4px 20px rgba(0,0,0,0.5)" }}>
          {toast}
        </div>
      )}

      <div style={{ maxWidth: 820, margin: "0 auto", padding: "32px 20px" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 28 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 10, letterSpacing: "0.18em", color: "#6b7280", textTransform: "uppercase", marginBottom: 6 }}>YANFLIX · Pipeline</div>
            {episodes.length > 1 ? (
              <select value={ep} onChange={e => setEp(e.target.value)} style={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, color: "#fff", fontSize: 18, fontWeight: 700, padding: "4px 10px", cursor: "pointer", maxWidth: 560 }}>
                {episodes.map(e => <option key={e.ep_folder} value={e.ep_folder}>{e.ep_folder}</option>)}
              </select>
            ) : (
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "#fff" }}>{ep || "No episodes"}</h1>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: n8nStatus === "online" ? "#22c55e" : n8nStatus === "offline" ? "#ef4444" : "#374151", display: "inline-block" }} />
              <span style={{ color: "#6b7280" }}>n8n {n8nStatus}</span>
            </div>
            <a href={`/studio?ep=${ep}`} style={{ fontSize: 11, color: "#6b7280", textDecoration: "none" }}>← Studio</a>
            <a href="http://localhost:5678" target="_blank" style={{ fontSize: 11, color: "#6b7280", textDecoration: "none" }}>n8n →</a>
          </div>
        </div>

        {/* n8n Autopilot */}
        <div style={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, padding: 16, marginBottom: 24, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 600, color: "#fff", marginBottom: 3 }}>🤖 n8n Autopilot</div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>Triggers the full remaining pipeline unattended. Harvest → Clone → Translate → Synth → Render.</div>
          </div>
          <button
            onClick={triggerN8nAutopilot}
            disabled={n8nStatus !== "online" || !ep}
            style={{ background: n8nStatus === "online" ? "#7c3aed" : "#374151", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", fontSize: 13, fontWeight: 600, cursor: n8nStatus === "online" ? "pointer" : "not-allowed", opacity: n8nStatus !== "online" ? 0.5 : 1, whiteSpace: "nowrap" }}
          >
            {n8nStatus === "offline" ? "n8n Offline" : "▶ Run Autopilot"}
          </button>
        </div>

        {/* Stage cards */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {STAGE_META.map(stage => {
            const detail = pipeStatus[`${stage.key}_detail`] ?? {};
            const status = pipeStatus[stage.key] ?? "offline";
            const b = badge(status);
            const isRunning = status === "processing";
            const isDone    = status === "done";
            const pct       = detail.progress ?? 0;
            const isBusy    = !!busy[stage.key];

            return (
              <div key={stage.key} style={{ background: "#0f172a", border: `1px solid ${isDone ? "#14532d" : isRunning ? "#451a03" : "#1e293b"}`, borderRadius: 10, padding: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>

                  {/* Status dot + label */}
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 200 }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%", background: b.dot, flexShrink: 0, display: "inline-block" }} />
                    <div>
                      <div style={{ fontWeight: 600, color: "#fff", fontSize: 14 }}>{stage.label}</div>
                      <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>{stage.desc}</div>
                    </div>
                  </div>

                  {/* Badge */}
                  <span style={{ background: b.bg, color: b.color, fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4, letterSpacing: "0.06em", whiteSpace: "nowrap" }}>
                    {b.text}
                  </span>

                  {/* Action button */}
                  {!isRunning && stage.api && (
                    <button
                      onClick={() => trigger(stage)}
                      disabled={isBusy || !ep}
                      style={{ background: isDone ? "#1e293b" : stage.color, color: isDone ? "#6b7280" : "#fff", border: "none", borderRadius: 7, padding: "7px 16px", fontSize: 12, fontWeight: 600, cursor: isBusy ? "not-allowed" : "pointer", whiteSpace: "nowrap", opacity: isBusy ? 0.7 : 1 }}
                    >
                      {isBusy ? "Starting…" : isDone ? "↺ Re-run" : "▶ Run"}
                    </button>
                  )}
                </div>

                {/* Progress bar */}
                {isRunning && (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                      <span style={{ fontSize: 11, color: "#94a3b8" }}>{detail.step ?? "processing…"}</span>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: "#94a3b8" }}>{pct}%</span>
                        {stage.cancelApi && (
                          <button
                            onClick={() => cancelStage(stage)}
                            style={{ background: "#7f1d1d", color: "#fca5a5", border: "none", borderRadius: 5, padding: "3px 10px", fontSize: 11, fontWeight: 600, cursor: "pointer" }}
                          >
                            ✕ Cancel
                          </button>
                        )}
                      </div>
                    </div>
                    <div style={{ width: "100%", height: 6, background: "#1e293b", borderRadius: 99, overflow: "hidden" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: stage.color, borderRadius: 99, transition: "width 1s ease" }} />
                    </div>
                  </div>
                )}

                {/* Done summary */}
                {isDone && detail.updated_at && (
                  <div style={{ marginTop: 8, fontSize: 11, color: "#4b5563" }}>
                    Completed {new Date(detail.updated_at).toLocaleString()}
                    {detail.error && <span style={{ color: "#fca5a5", marginLeft: 8 }}>— {detail.error}</span>}
                  </div>
                )}

                {/* Error */}
                {status === "error" && (
                  <div style={{ marginTop: 8, fontSize: 12, color: "#fca5a5" }}>✗ {detail.error ?? "Unknown error"}</div>
                )}
              </div>
            );
          })}
        </div>

        <div style={{ marginTop: 24, fontSize: 11, color: "#1f2937", textAlign: "center" }}>
          Auto-refreshes every 8s · Synth stages run via Python worker — trigger from n8n or CLI
        </div>
      </div>
    </div>
  );
}

export default function Pipeline() {
  return (
    <Suspense fallback={<div style={{ background: "#050505", minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "#4b5563", fontFamily: "system-ui" }}>Loading…</div>}>
      <PipelineInner />
    </Suspense>
  );
}
