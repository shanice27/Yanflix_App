"use client";
import { useState, useEffect, useRef, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";

const STAGES = [
  { key: "status_isolate",        label: "Isolate" },
  { key: "status_transcribe",     label: "Transcribe" },
  { key: "status_segment",        label: "Segment" },
  { key: "status_diarize",        label: "Diarize" },
  { key: "status_harvest",        label: "Harvest Seeds" },
  { key: "status_clone",          label: "Clone Voices" },
  { key: "status_translate",      label: "Translate" },
  { key: "status_synth_standard", label: "Synth (Std)" },
  { key: "status_synth_aave",     label: "Synth (AAVE)" },
  { key: "status_render_standard","label": "Render (Std)" },
  { key: "status_render_aave",    "label": "Render (AAVE)" },
];

const COLORS = [
  "#dc2626","#d97706","#16a34a","#2563eb","#7c3aed",
  "#db2777","#0891b2","#65a30d","#ea580c","#9333ea",
  "#0284c7","#15803d","#c2410c","#4338ca","#be185d",
];

function charColor(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return COLORS[h % COLORS.length];
}

function stageBadge(s: string) {
  if (s === "done")       return { bg: "#14532d", color: "#86efac", dot: "#22c55e", label: "DONE" };
  if (s === "processing") return { bg: "#451a03", color: "#fcd34d", dot: "#f59e0b", label: "RUNNING" };
  if (s === "error")      return { bg: "#450a0a", color: "#fca5a5", dot: "#ef4444", label: "ERROR" };
  return { bg: "#111827", color: "#4b5563", dot: "#374151", label: "—" };
}

type Line = { line_index: number; character: string; start: number; end: number; source_text: string; detected_emotion: string; type: string };
type CharGroup = { name: string; line_count: number; lines: Line[] };
type Episode = { ep_folder: string; show_slug: string; scene_context: boolean; diarize_status: string };

function StudioInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const [episodes, setEpisodes]     = useState<Episode[]>([]);
  const [ep, setEp]                 = useState(searchParams.get("ep") || "");
  const [pipeStatus, setPipeStatus] = useState<Record<string, string>>({});
  const [chars, setChars]           = useState<CharGroup[]>([]);
  const [castLocked, setCastLocked] = useState(false);
  const [renames, setRenames]       = useState<Record<string, string>>({});
  const [expanded, setExpanded]     = useState<string | null>(null);
  const [saving, setSaving]         = useState(false);
  const [toast, setToast]           = useState("");
  const [lineEdits, setLineEdits]   = useState<Record<number, string>>({});
  const [pendingLines, setPending]  = useState<Set<number>>(new Set());
  const [diarizeSpkStatus, setDSS]  = useState<any>(null);
  const [rediarizeBusy, setRDB]     = useState(false);
  const [diarizeDetail, setDD]      = useState<any>(null);
  const pollRef  = useRef<NodeJS.Timeout | null>(null);
  const saveTimer = useRef<NodeJS.Timeout | null>(null);

  // ---------- episode list ----------

  useEffect(() => {
    fetch("/api/episodes").then(r => r.json()).then((data: Episode[]) => {
      setEpisodes(data);
      if (!ep && data.length > 0) setEp(data[0].ep_folder);
    }).catch(() => {});
  }, []);

  const currentEpInfo = episodes.find(e => e.ep_folder === ep);

  // ---------- data fetching ----------

  const fetchStatus = () => {
    if (!ep) return;
    fetch(`/api/status?ep_folder=${ep}`).then(r => r.json())
      .then(d => {
        setPipeStatus(d);
        const detail = d.status_diarize_detail ?? null;
        setDD(detail);
        // Slow poll back down once diarize is no longer running
        if (detail?.status !== "processing" && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = setInterval(() => { fetchStatus(); fetchCast(); fetchDSS(); }, 10000);
        }
      }).catch(() => {});
  };

  const fetchCast = () => {
    if (!ep) return;
    fetch(`/api/cast-review?ep_folder=${ep}`).then(r => r.json())
      .then(d => {
        setCastLocked(!!d.cast_locked);
        const groups: CharGroup[] = d.characters || [];
        setChars(groups);
        setRenames(prev => {
          const init: Record<string, string> = {};
          groups.forEach((c: any) => { if (!prev[c.name]) init[c.name] = c.name; });
          return { ...init, ...prev };
        });
      }).catch(() => {});
  };

  const fetchDSS = () => {
    if (!ep) return;
    fetch(`/api/diarize-speakers?ep_folder=${ep}`).then(r => r.json())
      .then(d => setDSS(d)).catch(() => {});
  };

  useEffect(() => {
    if (!ep) return;
    // Reset state when episode changes
    setPipeStatus({});
    setChars([]);
    setCastLocked(false);
    setRenames({});
    setExpanded(null);
    setLineEdits({});
    setPending(new Set());
    setDSS(null);

    fetchStatus(); fetchCast(); fetchDSS();
    router.replace(`/studio?ep=${encodeURIComponent(ep)}`, { scroll: false });

    if (pollRef.current) clearInterval(pollRef.current);
    const interval = diarizeDetail?.status === "processing" ? 5000 : 10000;
    pollRef.current = setInterval(() => { fetchStatus(); fetchCast(); fetchDSS(); }, interval);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [ep]);

  // ---------- actions ----------

  const runPyannote = async () => {
    setDSS({ status: "processing", step: "starting" });
    await fetch("/api/diarize-speakers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ep_folder: ep, show_name: currentEpInfo?.show_slug ?? ep }),
    });
    showToast("Pyannote running — takes 5–15 min, page will update automatically");
    fetchDSS();
  };

  const cancelDiarize = async () => {
    await fetch("/api/diarize/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ep_folder: ep }),
    });
    showToast("Diarize cancelled");
    setTimeout(fetchStatus, 1000);
  };

  const markDiarizeDone = async () => {
    await fetch("/api/diarize/cancel", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ep_folder: ep, line_count: chars.reduce((s, c) => s + c.line_count, 0) }),
    });
    showToast("✓ Diarize marked done — cast locked");
    setTimeout(() => { fetchStatus(); fetchCast(); }, 1000);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => { fetchStatus(); fetchCast(); fetchDSS(); }, 10000);
  };

  const rerunDiarize = async () => {
    setRDB(true);
    try {
      await fetch("/api/diarize", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ep_folder: ep,
          source_lang: "ja",
          show_name: currentEpInfo?.show_slug ?? ep,
          force: true,
        }),
      });
      showToast(
        currentEpInfo?.scene_context
          ? "Re-running diarize with scene context ✓"
          : "Re-running diarize (no scene context found for this episode)"
      );
      // Switch to fast polling immediately
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(() => { fetchStatus(); fetchCast(); fetchDSS(); }, 5000);
      setTimeout(fetchStatus, 1500);
    } catch (e: any) {
      showToast("Error: " + e.message);
    }
    setRDB(false);
  };

  // ---------- line reassignment ----------

  const reassignLine = (lineIndex: number, newChar: string) => {
    setLineEdits(e => ({ ...e, [lineIndex]: newChar }));
    setPending(p => new Set(p).add(lineIndex));
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      setPending(p => {
        const ids = [...p];
        setLineEdits(cur => {
          const updates = ids.map(id => ({ line_index: id, character: cur[id] })).filter(u => u.character);
          fetch("/api/cast-review", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ep_folder: ep, line_updates: updates }),
          }).then(() => { showToast(`✓ ${updates.length} line${updates.length > 1 ? "s" : ""} reassigned`); fetchCast(); });
          return cur;
        });
        return new Set();
      });
    }, 600);
  };

  // ---------- save cast ----------

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 4000); };

  const saveCast = async () => {
    setSaving(true);
    try {
      await fetch("/api/cast-review", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ep_folder: ep, renames }),
      });
      const r = await fetch("/api/save_cast", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ep_folder: ep, show_name: currentEpInfo?.show_slug ?? ep, source_lang: "ja" }),
      });
      const d = await r.json();
      if (d.cast_locked) { showToast("✓ Cast saved and locked"); fetchCast(); }
      else showToast("Error: " + JSON.stringify(d));
    } catch (e: any) { showToast("Error: " + e.message); }
    setSaving(false);
  };

  // ---------- helpers ----------

  const allNames = [...new Set(Object.values(renames).map(n => n.trim()).filter(Boolean))].sort();

  function fmt(sec: number) {
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  // ---------- render ----------

  return (
    <div style={{ fontFamily: "'Inter', system-ui, sans-serif", background: "#050505", minHeight: "100vh", color: "#e5e7eb" }}>

      {/* Toast */}
      {toast && (
        <div style={{ position: "fixed", top: 20, right: 20, zIndex: 999, background: toast.startsWith("✓") ? "#14532d" : "#7f1d1d", color: "#fff", padding: "12px 20px", borderRadius: 10, fontSize: 14, fontWeight: 500, boxShadow: "0 4px 20px rgba(0,0,0,0.5)" }}>
          {toast}
        </div>
      )}

      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "32px 20px" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 28 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 10, letterSpacing: "0.18em", color: "#6b7280", textTransform: "uppercase", marginBottom: 6 }}>YANFLIX · Pipeline Control</div>

            {/* Episode selector */}
            {episodes.length > 1 ? (
              <select
                value={ep}
                onChange={e => { setEp(e.target.value); }}
                style={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, color: "#fff", fontSize: 18, fontWeight: 700, padding: "4px 10px", cursor: "pointer", marginBottom: 4, maxWidth: 560 }}
              >
                {episodes.map(e => (
                  <option key={e.ep_folder} value={e.ep_folder}>{e.ep_folder}</option>
                ))}
              </select>
            ) : (
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "#fff", letterSpacing: "-0.01em" }}>{ep || "No episodes found"}</h1>
            )}

            {/* Scene context badge */}
            {currentEpInfo && (
              <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8 }}>
                {currentEpInfo.scene_context ? (
                  <span style={{ fontSize: 11, background: "#14532d", color: "#86efac", padding: "2px 8px", borderRadius: 4, fontWeight: 600, letterSpacing: "0.04em" }}>
                    ✓ SCENE CONTEXT LOADED
                  </span>
                ) : (
                  <span style={{ fontSize: 11, background: "#1c1917", color: "#78716c", padding: "2px 8px", borderRadius: 4, fontWeight: 600, letterSpacing: "0.04em" }}>
                    NO SCENE CONTEXT — diarizer will guess
                  </span>
                )}
                {currentEpInfo.show_slug && (
                  <span style={{ fontSize: 11, color: "#4b5563" }}>{currentEpInfo.show_slug}</span>
                )}
              </div>
            )}
          </div>
          <a href="http://localhost:5678" target="_blank" style={{ fontSize: 11, color: "#6b7280", textDecoration: "none", marginTop: 8 }}>n8n →</a>
        </div>

        {/* Pipeline stages */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 24 }}>
          {STAGES.map(s => {
            const st = pipeStatus[s.key] || "offline";
            const b = stageBadge(st);
            return (
              <div key={s.key} style={{ background: b.bg, border: `1px solid ${b.dot}22`, borderRadius: 6, padding: "5px 10px", display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: b.dot, display: "inline-block", flexShrink: 0 }} />
                <span style={{ fontSize: 11, color: b.color, whiteSpace: "nowrap" }}>{s.label}</span>
              </div>
            );
          })}
        </div>

        {/* Re-run Diarize */}
        {(() => {
          const ds = diarizeDetail;
          const isRunning = ds?.status === "processing";
          const isDone    = ds?.status === "done";
          const isError   = ds?.status === "error";
          const pct       = ds?.progress ?? 0;
          const chunkInfo = ds?.chunks_done != null ? `chunk ${ds.chunks_done}/${ds.chunks_total}` : ds?.step ?? "";
          return (
            <div style={{ background: "#0f172a", border: `1px solid ${isError ? "#7f1d1d" : isDone ? "#14532d" : "#1e293b"}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontWeight: 600, color: "#fff", marginBottom: 3 }}>↺ Re-run Diarize (LLM Speaker Naming)</div>
                  <div style={{ fontSize: 12, color: "#6b7280" }}>
                    {currentEpInfo?.scene_context
                      ? "Scene context loaded — character roster and timestamps injected into prompt."
                      : "No scene context for this episode. Create one in characters/shows/<show>/ to improve accuracy."}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {isRunning && (
                    <button onClick={cancelDiarize} style={{ background: "#7f1d1d", color: "#fca5a5", border: "none", borderRadius: 8, padding: "9px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}>
                      ⬛ Cancel
                    </button>
                  )}
                  {!isRunning && !isDone && (
                    <button onClick={markDiarizeDone} style={{ background: "#14532d", color: "#86efac", border: "none", borderRadius: 8, padding: "9px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}>
                      ✓ Mark Done
                    </button>
                  )}
                  {!isRunning && (
                    <button
                      onClick={rerunDiarize}
                      disabled={rediarizeBusy || !ep}
                      style={{ background: currentEpInfo?.scene_context ? "#2563eb" : "#374151", color: "#fff", border: "none", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 600, cursor: rediarizeBusy || !ep ? "not-allowed" : "pointer", opacity: rediarizeBusy ? 0.7 : 1, whiteSpace: "nowrap" }}
                    >
                      {rediarizeBusy ? "Starting…" : isDone ? "↺ Re-run Again" : "↺ Re-run Diarize"}
                    </button>
                  )}
                  {isDone && (
                    <button onClick={async () => { await saveCast(); router.push(`/pipeline?ep=${encodeURIComponent(ep)}`); }} style={{ background: "#7c3aed", color: "#fff", border: "none", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}>
                      🔒 Lock & Go to Pipeline →
                    </button>
                  )}
                </div>
              </div>

              {/* Progress bar */}
              {isRunning && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 12, color: "#93c5fd" }}>{chunkInfo || "processing…"}</span>
                    <span style={{ fontSize: 13, fontWeight: 700, color: "#93c5fd" }}>{pct}%</span>
                  </div>
                  <div style={{ width: "100%", height: 8, background: "#1e293b", borderRadius: 99, overflow: "hidden" }}>
                    <div style={{ width: `${pct}%`, height: "100%", background: "#2563eb", borderRadius: 99, transition: "width 0.8s ease" }} />
                  </div>
                  <div style={{ fontSize: 11, color: "#4b5563", marginTop: 6 }}>
                    Polling every 5s — next chunk fires after 30s cooldown (Groq rate limit)
                  </div>
                </div>
              )}

              {isDone && (
                <div style={{ marginTop: 10, fontSize: 12, color: "#86efac" }}>
                  ✓ Done — {ds.line_count} lines assigned · {ds.song_count ?? 0} songs detected
                </div>
              )}
              {isError && (
                <div style={{ marginTop: 10, fontSize: 12, color: "#fca5a5" }}>
                  ✗ Error: {ds.error}
                </div>
              )}
            </div>
          );
        })()}

        {/* Pyannote speaker diarization panel */}
        <div style={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 10, padding: 20, marginBottom: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
            <div>
              <div style={{ fontWeight: 600, color: "#fff", marginBottom: 4 }}>🎙 Voice-Based Speaker ID</div>
              <div style={{ fontSize: 12, color: "#6b7280", maxWidth: 540 }}>
                Pyannote groups lines by <em>actual voice</em> — not text guesses. Then pitch analysis estimates gender per speaker.
                Groq then names characters — much more accurate than text-only.
              </div>
            </div>
            {(!diarizeSpkStatus || ["not_started","done","error","stopped"].includes(diarizeSpkStatus.status)) && (
              <button
                onClick={runPyannote}
                style={{ background: "#7c3aed", color: "#fff", border: "none", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}
              >
                {diarizeSpkStatus?.status === "done" ? "↺ Re-run Pyannote" : "Run Pyannote + Gender Analysis"}
              </button>
            )}
            {diarizeSpkStatus?.status === "processing" && (
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 180, height: 6, background: "#1e293b", borderRadius: 99, overflow: "hidden" }}>
                  <div style={{ width: `${diarizeSpkStatus.progress || 0}%`, height: "100%", background: "#7c3aed", transition: "width 0.5s" }} />
                </div>
                <span style={{ fontSize: 12, color: "#a78bfa" }}>{diarizeSpkStatus.step || "running"}… {diarizeSpkStatus.progress || 0}%</span>
                <button
                  onClick={async () => {
                    await fetch(`/api/diarize-speakers?ep_folder=${encodeURIComponent(ep)}`, { method: 'DELETE' });
                    setDSS({ status: 'stopped' });
                  }}
                  style={{ background: "#dc2626", color: "#fff", border: "none", borderRadius: 6, padding: "5px 12px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
                >
                  ⬛ Stop
                </button>
              </div>
            )}
          </div>

          {diarizeSpkStatus?.status === "done" && diarizeSpkStatus.gender_map && (
            <div style={{ marginTop: 16, display: "flex", flexWrap: "wrap", gap: 8 }}>
              {Object.entries(diarizeSpkStatus.gender_map).map(([spk, gender]: any) => {
                const char = diarizeSpkStatus.char_map?.[spk];
                return (
                  <div key={spk} style={{ background: "#1e293b", borderRadius: 6, padding: "5px 12px", fontSize: 12 }}>
                    <span style={{ color: "#6b7280" }}>{spk} </span>
                    <span style={{ color: gender === "female" ? "#f9a8d4" : "#93c5fd" }}>{gender === "female" ? "♀" : "♂"}</span>
                    {char && <span style={{ color: "#e2e8f0", marginLeft: 6 }}>→ {char}</span>}
                  </div>
                );
              })}
            </div>
          )}
          {diarizeSpkStatus?.status === "error" && (
            <div style={{ marginTop: 10, fontSize: 12, color: "#fca5a5" }}>Error: {diarizeSpkStatus.error}</div>
          )}
        </div>

        {/* Cast section header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 10, letterSpacing: "0.15em", color: "#6b7280", textTransform: "uppercase", marginBottom: 4 }}>Script Director · Cast Review</div>
            <div style={{ fontSize: 14, color: "#9ca3af" }}>
              {chars.length} characters · {chars.reduce((s, c) => s + c.line_count, 0)} lines
              {castLocked && <span style={{ marginLeft: 10, background: "#14532d", color: "#86efac", fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4, letterSpacing: "0.06em" }}>LOCKED</span>}
            </div>
          </div>
          <button
            onClick={saveCast}
            disabled={saving || !ep}
            style={{ background: "#dc2626", color: "#fff", border: "none", borderRadius: 8, padding: "9px 20px", fontSize: 13, fontWeight: 600, cursor: saving || !ep ? "not-allowed" : "pointer", opacity: saving ? 0.7 : 1 }}
          >
            {saving ? "Saving…" : castLocked ? "💾 Re-save Cast" : "🔒 Save & Lock Cast"}
          </button>
        </div>

        <div style={{ fontSize: 12, color: "#4b5563", marginBottom: 20 }}>
          Rename in the field below each card. Type the same name on two cards to merge them. Click a card to see all its lines.
        </div>

        {/* Character cards grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
          {chars.map(c => {
            const currentName = renames[c.name] ?? c.name;
            const color = charColor(currentName || c.name);
            const isOpen = expanded === c.name;
            const isDuplicate = chars.some(other => other.name !== c.name && (renames[other.name] ?? other.name) === currentName && currentName !== "");

            return (
              <div key={c.name} style={{ gridColumn: isOpen ? "1 / -1" : undefined }}>
                <div style={{ background: "#0f172a", border: `1px solid ${isOpen ? color + "55" : "#1e293b"}`, borderRadius: 10, overflow: "hidden", transition: "border-color 0.15s" }}>

                  {/* Card header */}
                  <div onClick={() => setExpanded(isOpen ? null : c.name)} style={{ padding: "14px 16px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ width: 40, height: 40, borderRadius: "50%", background: color, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, fontWeight: 700, color: "#fff", flexShrink: 0 }}>
                      {(currentName || c.name).slice(0, 2).toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, color: "#fff", fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {currentName || c.name}
                        {isDuplicate && <span style={{ marginLeft: 8, fontSize: 10, background: "#78350f", color: "#fcd34d", padding: "1px 6px", borderRadius: 3 }}>WILL MERGE</span>}
                      </div>
                      <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>
                        {c.line_count} lines · click to {isOpen ? "collapse" : "view lines"}
                      </div>
                    </div>
                    <div style={{ fontSize: 18, color: "#374151", flexShrink: 0 }}>{isOpen ? "▲" : "▼"}</div>
                  </div>

                  {/* Rename field */}
                  <div style={{ padding: "0 16px 14px", display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                      value={currentName}
                      onClick={e => e.stopPropagation()}
                      onChange={e => setRenames(r => ({ ...r, [c.name]: e.target.value }))}
                      list="char-names"
                      placeholder="Rename character…"
                      style={{ flex: 1, background: "#1e293b", border: "1px solid #334155", borderRadius: 6, padding: "6px 10px", color: "#fff", fontSize: 12 }}
                    />
                    {currentName !== c.name && (
                      <button
                        onClick={e => { e.stopPropagation(); setRenames(r => ({ ...r, [c.name]: c.name })); }}
                        title="Reset"
                        style={{ background: "none", border: "none", color: "#6b7280", cursor: "pointer", fontSize: 16, padding: "0 4px" }}
                      >↩</button>
                    )}
                  </div>

                  {/* Expanded lines */}
                  {isOpen && (
                    <div style={{ borderTop: "1px solid #1e293b", maxHeight: 500, overflowY: "auto" }}>
                      {c.lines.map((l: any) => {
                        const hasEn = !!l.text_standard;
                        const currentChar = lineEdits[l.line_index] ?? l.character;
                        const isPending = pendingLines.has(l.line_index);
                        return (
                          <div key={l.line_index} style={{ padding: "10px 16px", borderBottom: "1px solid #0f172a", display: "grid", gridTemplateColumns: "38px 76px 1fr 160px", gap: 10, alignItems: "start", fontSize: 12, background: isPending ? "#0f1f0f" : undefined }}>
                            <div style={{ color: "#4b5563", fontVariantNumeric: "tabular-nums", paddingTop: 2 }}>#{l.line_index}</div>
                            <div style={{ color: "#6b7280", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", paddingTop: 2 }}>
                              {fmt(l.start)} – {fmt(l.end)}
                            </div>
                            <div>
                              {hasEn
                                ? <div style={{ color: "#f1f5f9", lineHeight: 1.5, marginBottom: 3 }}>{l.text_standard}</div>
                                : <div style={{ color: "#4b5563", fontStyle: "italic", fontSize: 11, marginBottom: 3 }}>translating…</div>
                              }
                              <div style={{ color: "#374151", fontSize: 11, lineHeight: 1.4 }}>{l.source_text}</div>
                              <div style={{ color: "#1f2937", fontSize: 10, marginTop: 2 }}>
                                [{l.detected_emotion}]{l.type === "singing" ? " 🎵" : ""}
                              </div>
                            </div>
                            <div style={{ paddingTop: 1 }}>
                              <select
                                value={currentChar}
                                onChange={e => reassignLine(l.line_index, e.target.value)}
                                style={{ width: "100%", background: currentChar !== l.character ? "#1a2e1a" : "#1e293b", border: `1px solid ${currentChar !== l.character ? "#16a34a55" : "#334155"}`, borderRadius: 5, padding: "4px 6px", color: currentChar !== l.character ? "#86efac" : "#94a3b8", fontSize: 11, cursor: "pointer" }}
                              >
                                {allNames.map(n => <option key={n} value={n}>{n}</option>)}
                              </select>
                              {currentChar !== l.character && (
                                <div style={{ fontSize: 9, color: "#16a34a", marginTop: 2 }}>was: {l.character}</div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <datalist id="char-names">
          {allNames.map(n => <option key={n} value={n} />)}
        </datalist>

        <div style={{ marginTop: 28, fontSize: 11, color: "#1f2937", textAlign: "center" }}>
          Auto-refreshes every 10s
        </div>
      </div>
    </div>
  );
}

export default function Studio() {
  return (
    <Suspense fallback={<div style={{ background: "#050505", minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "#4b5563", fontFamily: "system-ui" }}>Loading…</div>}>
      <StudioInner />
    </Suspense>
  );
}
