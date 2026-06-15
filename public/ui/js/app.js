const { useState, useEffect, useRef, useLayoutEffect, useCallback } = React;
const { I, Star, useIndicator } = window;

/* ==========================================================================
   🔊 PROCEDURAL WAVEFORM TIMELINE COMPONENTS
   ========================================================================== */
function Waveform({ scene }) {
  const rootRef = useRef(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const DURATION = scene.dur;
    const STORE = "yanflix_dub_" + scene.id;
    const N = 140;

    const waveOrig = root.querySelector(".wave.orig");
    const waveDub = root.querySelector(".wave.dub");
    const ruler = root.querySelector(".ruler");
    const playhead = root.querySelector(".playhead");
    const curEl = root.querySelector(".cur");
    const playIcon = root.querySelector(".play-icon");
    const statusEl = root.querySelector(".audio-status");
    const statusText = root.querySelector(".audio-status .txt");
    waveOrig.innerHTML = ""; waveDub.innerHTML = ""; ruler.innerHTML = "";

    function makeBars(container, seed, speechy) {
      const bars = []; let r = seed;
      const rnd = () => { r = (r * 9301 + 49297) % 233280; return r / 233280; };
      for (let i = 0; i < N; i++) {
        const env = 0.5 + 0.5 * Math.sin(i * 0.13 + seed);
        const gap = rnd() < 0.08 ? 0.12 : 1;
        let h = (0.18 + rnd() * 0.82) * env * gap;
        if (speechy) h = Math.max(0.08, h * (0.6 + 0.4 * Math.sin(i * 0.5)));
        const bar = document.createElement("div");
        bar.className = "bar";
        bar.style.height = Math.max(8, Math.round(h * 84)) + "%";
        container.appendChild(bar); bars.push(bar);
      }
      return bars;
    }
    const barsOrig = makeBars(waveOrig, 17 + scene.dur, false);
    const barsDub = makeBars(waveDub, 88 + scene.dur, true);
    
    function fmt(sec) {
      sec = Math.max(0, Math.floor(sec));
      const m = Math.floor(sec / 60), s = sec % 60;
      return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    }

    for (let t = 0; t <= 6; t++) {
      const s = document.createElement("span");
      s.textContent = fmt((DURATION / 6) * t);
      ruler.appendChild(s);
    }
    root.querySelector(".dur").textContent = fmt(DURATION);

    let progress = 0, playing = false, rafId = null, lastTs = 0, lastOn = -1, loop = false, recOn = false;

    function paint() {
      const onCount = Math.round(progress * N);
      if (onCount !== lastOn) {
        const lo = Math.min(onCount, lastOn < 0 ? 0 : lastOn);
        const hi = Math.max(onCount, lastOn);
        for (let i = lo; i <= hi && i < N; i++) {
          if (i < 0) continue;
          const on = i < onCount;
          barsOrig[i].classList.toggle("on", on);
          barsDub[i].classList.toggle("on", on);
        }
        lastOn = onCount;
      }
      const pct = progress * 100;
      playhead.style.left = "calc(10px + " + pct + "% - " + (pct * 20 / 100) + "px)";
      curEl.textContent = fmt(progress * DURATION);
      try { localStorage.setItem(STORE, String(progress)); } catch (e) {}
    }
    function setProgress(p) { progress = Math.max(0, Math.min(1, p)); paint(); }
    function setStatus(kind) {
      statusEl.className = "status audio-status" + (kind === "recording" ? " processing" : "");
      statusText.textContent = kind === "recording" ? "Recording" : kind === "playing" ? "Playing" : "Ready";
    }
    function tick(ts) {
      if (!lastTs) lastTs = ts;
      const dt = (ts - lastTs) / 1000; lastTs = ts;
      progress += dt / DURATION;
      if (progress >= 1) { if (loop) progress = 0; else { progress = 1; paint(); stop(); return; } }
      paint(); rafId = requestAnimationFrame(tick);
    }
    function play() {
      if (playing) return;
      if (progress >= 1) progress = 0;
      playing = true; lastTs = 0;
      playIcon.innerHTML = '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/>';
      setStatus("playing"); rafId = requestAnimationFrame(tick);
    }
    function stop() {
      playing = false; if (rafId) cancelAnimationFrame(rafId);
      playIcon.innerHTML = '<path d="M7 5v14l12-7z"/>';
      if (!recOn) setStatus("ready");
    }

    const onClicks = [];
    root.querySelectorAll(".transport .tbtn").forEach(btn => {
      const h = () => {
        const act = btn.getAttribute("data-act");
        if (act === "play") playing ? stop() : play();
        else if (act === "back") setProgress(progress - 5 / DURATION);
        else if (act === "fwd") setProgress(progress + 5 / DURATION);
        else if (act === "loop") { loop = !loop; btn.classList.toggle("on", loop); }
        else if (act === "rec") {
          recOn = !recOn; btn.classList.toggle("on", recOn);
          if (recOn) { setStatus("recording"); play(); } else setStatus(playing ? "playing" : "ready");
        }
      };
      btn.addEventListener("click", h); onClicks.push([btn, h]);
    });
    const seekHandlers = [];
    [waveOrig, waveDub].forEach(w => {
      const h = (e) => {
        const rect = w.getBoundingClientRect();
        setProgress((e.clientX - rect.left - 10) / (rect.width - 20));
      };
      w.addEventListener("click", h); seekHandlers.push([w, h]);
    });
    const vol = root.querySelector(".vol input");
    const paintVol = () => { vol.style.setProperty("--p", vol.value + "%"); vol.style.background = "linear-gradient(90deg, var(--red) 0 " + vol.value + "%, var(--track) " + vol.value + "% 100%)"; };
    vol.addEventListener("input", paintVol); paintVol();

    const saved = parseFloat(localStorage.getItem(STORE));
    setProgress(isNaN(saved) ? 0 : saved);

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      onClicks.forEach(([b, h]) => b.removeEventListener("click", h));
      seekHandlers.forEach(([w, h]) => w.removeEventListener("click", h));
      vol.removeEventListener("input", paintVol);
    };
  }, [scene.id]);

  return (
    <div className="panel" ref={rootRef} style={{ marginTop: 16 }}>
      <div className="panel-head" style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div className="id" style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}><h3>{scene.name}</h3><span className="badge lang">EN &rarr; ES</span></div>
        <div className="time" style={{ fontFamily: 'JetBrains Mono, monospace' }}><span className="cur">00:00</span> / <span className="dur">00:00</span></div>
      </div>
      <div className="ruler"></div>
      <div className="lanes">
        <div className="lane orig"><span className="lane-tag">Original</span><div className="wave orig"></div></div>
        <div className="lane dub"><span className="lane-tag">Your dub</span><div className="wave dub"></div></div>
        <div className="playhead"></div>
      </div>
      <div className="audio-controls" style={{ display: 'flex', gap: 16, marginTop: 12, alignItems: 'center' }}>
        <div className="transport">
          <button className="tbtn" data-act="back" title="Back 5s"><svg viewBox="0 0 24 24"><path d="M11 6V3L6 7l5 4V8a4.5 4.5 0 1 1-4.5 4.5H4A6.5 6.5 0 1 0 11 6z"/></svg></button>
          <button className="tbtn play" data-act="play"><svg viewBox="0 0 24 24" className="play-icon"><path d="M7 5v14l12-7z"/></svg></button>
          <button className="tbtn" data-act="fwd" title="Forward 5s"><svg viewBox="0 0 24 24"><path d="M13 6V3l5 4-5 4V8a4.5 4.5 0 1 0 4.5 4.5H20A6.5 6.5 0 1 1 13 6z"/></svg></button>
          <button className="tbtn rec toggle" data-act="rec" title="Record take"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="6"/></svg></button>
          <button className="tbtn toggle" data-act="loop" title="Loop"><svg viewBox="0 0 24 24"><path d="M7 7h8v3l5-4-5-4v3H5v6h2zm10 10H9v-3l-5 4 5 4v-3h10v-6h-2z"/></svg></button>
        </div>
        <div className="vol">
          <svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 5V4L8 9zm12 .5a4 4 0 0 1 0 5v-5z"/></svg>
          <input type="range" min="0" max="100" defaultValue="80" />
        </div>
        <span className="spacer"></span>
        <span className="status audio-status"><span className="led"></span><span className="txt">Ready</span></span>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   📝 PIPELINE SUB-VIEW CONTEXTS
   -------------------------------------------------------------------------- */
function ExportPanel() {
  const [sent, setSent] = useState(false);
  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-head"><h2>Export &amp; reattach</h2><span className="badge">Final dub &rarr; video</span></div>
      <div style={{ display: "flex", flexDirection: "column", gap: 20, marginBottom: 24 }}>
        <div className="bar-row"><div className="bar-head"><span>Rendering dialogue stems</span><span className="pct">100%</span></div><div className="track"><div className="fill green" style={{ width: "100%" }}></div></div></div>
        <div className="bar-row"><div className="bar-head"><span>Mixing master</span><span className="pct">100%</span></div><div className="track"><div className="fill green" style={{ width: "100%" }}></div></div></div>
        <div className="bar-row"><div className="bar-head"><span>Muxing audio to video</span><span className="pct">{sent ? "100%" : "94%"}</span></div><div className="track"><div className="fill" style={{ width: sent ? "100%" : "94%" }}></div></div></div>
      </div>
      <button className={"btn " + (sent ? "btn-gold" : "btn-primary")} onClick={() => setSent(true)}>
        {sent ? "Sent to Library" : "Send to video"}
      </button>
    </div>
  );
}

function StepPanel({ step }) {
  if (step === "Script") {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Script &amp; translation</h2><span className="badge">EN &rarr; ES · 4 lines</span></div>
        {window.SCRIPT.map((l, i) => (
          <div className="script-line" key={i} style={{ padding: '12px 0', borderBottom: '1px solid var(--line)' }}>
            <div className="who" style={{ fontWeight: 600, color: 'var(--gold)' }}>{l.who} <span style={{ fontSize: '11px', color: 'var(--ink-3)' }}>{l.vc}</span></div>
            <div className="lines" style={{ margin: '6px 0' }}><div className="src" style={{ opacity: 0.6 }}>{l.src}</div><div className="dub" style={{ color: '#fff' }}>{l.dub}</div></div>
            <div className="tc" style={{ fontSize: '11px', fontFamily: 'JetBrains Mono, monospace', opacity: 0.4 }}>{l.tc}</div>
          </div>
        ))}
      </div>
    );
  }
  if (step === "Mixer") {
    const stems = [["Dialogue", 88, "var(--red)"], ["Music", 54, "var(--gold)"], ["Ambience / SFX", 41, "var(--gold)"]];
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Mixer</h2><span className="badge">Stem balance</span></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          {stems.map(([n, v, c]) => (
            <div className="bar-row" key={n}>
              <div className="bar-head"><span>{n}</span><span className="pct">{v}%</span></div>
              <input type="range" min="0" max="100" defaultValue={v} style={{ background: `linear-gradient(90deg, ${c} 0 ${v}%, var(--track) ${v}% 100%)` }} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (step === "Voices") {
    const map = [["Speaker 1", "Mara Voss", "ES · Warm"], ["Speaker 2", "Kenji Arai", "ES · Gravel"], ["Speaker 3", "Diego Salas", "ES · Smooth"]];
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Voice casting</h2><span className="badge">3 speakers</span></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {map.map(([sp, actor, vc]) => (
            <div className="scene-item" key={sp} style={{ display: 'flex', justifyContent: 'space-between', padding: 12, background: 'var(--bg-2)', marginBottom: 6, borderRadius: 6 }}>
              <div className="sbody"><div className="sname" style={{ fontWeight: 600 }}>{actor}</div><div className="sline" style={{ fontSize: 12, opacity: 0.5 }}>{sp} · {vc}</div></div>
              <button className="btn btn-small" style={{ padding: "4px 12px" }}>Swap</button>
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (step === "Vocal Isolation") {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Vocal isolation</h2></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="bar-row"><div className="bar-head"><span>Separating vocals</span><span className="pct">72%</span></div><div className="track"><div className="fill" style={{ width: "72%" }}></div></div></div>
          <div className="bar-row"><div className="bar-head"><span>Extracting music bed</span><span className="pct">100%</span></div><div className="track"><div className="fill green" style={{ width: "100%" }}></div></div></div>
        </div>
      </div>
    );
  }
  if (step === "Speakers") {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Detected speakers</h2><span className="badge">Diarization</span></div>
        <div className="pill-row" style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          {["Speaker 1 · 4:12", "Speaker 2 · 3:48", "Speaker 3 · 1:06"].map(s => <span className="badge" key={s} style={{ padding: "8px 14px", fontSize: 12.5, background: 'var(--bg-2)', borderRadius: 4 }}>{s}</span>)}
        </div>
      </div>
    );
  }
  if (step === "Source Media") {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Source media</h2><span className="badge">1080p · 24fps · 18:42</span></div>
        <image-slot id="source-still" shape="rounded" radius="10" placeholder="Drop the source video frame" style={{ width: "100%", aspectRatio: "16/9", height: "auto", display: 'block' }}></image-slot>
      </div>
    );
  }
  if (step === "Export") {
    return <ExportPanel />;
  }
  return null;
}

function ProjectPicker({ shows, selectedShowId, onSelectShow, episodes, activeEp, onSetActive, onAddSlot, onNewProject, onNext }) {
  const show = shows.find(s => s.id === selectedShowId) || shows[0];
  const eps = episodes[show.id] || [];
  return (
    <div className="project-pick fade-in" style={{ display: 'flex', gap: 20, width: '100%' }}>
      <div className="pp-left" style={{ width: 280, flexShrink: 0 }}>
        <button className="btn btn-primary" style={{ width: "100%", justifyContent: "center", marginBottom: 18 }} onClick={onNewProject}>
          New Project
        </button>
        <div className="show-list">
          {shows.map(s => (
            <div key={s.id} className={"show-card" + (s.id === selectedShowId ? " active" : "")} onClick={() => onSelectShow(s.id)} style={{ padding: 12, background: s.id === selectedShowId ? 'var(--bg-3)' : 'var(--bg-1)', border: '1px solid var(--line)', marginBottom: 8, borderRadius: 6 }}>
              <div className="st" style={{ fontWeight: 600 }}>{s.title}</div>
              <div className="sm" style={{ fontSize: 11, opacity: 0.5, marginTop: 4 }}><span>{s.year}</span> · <span>{(episodes[s.id] || []).length} ep</span></div>
            </div>
          ))}
        </div>
      </div>

      <div className="pp-right" style={{ flex: 1 }}>
        <div className="pp-right-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ margin: 0 }}>{show.title} &mdash; Active Seasons</h2>
          <button className="btn btn-small" onClick={() => onAddSlot(show.id)}>+ Add Episode Slot</button>
        </div>
        <div className="ep-list" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {eps.map(ep => {
            const isActive = activeEp.showId === show.id && activeEp.code === ep.code;
            return (
              <div key={ep.code} className={"ep-row" + (isActive ? " active" : "")} onClick={() => onSetActive({ showId: show.id, code: ep.code })} style={{ display: 'flex', justifyContent: 'space-between', padding: 12, background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 6, alignItems: 'center' }}>
                <span className="code" style={{ fontWeight: 700, color: 'var(--gold)' }}>{ep.code}</span>
                <span className="epname" style={{ flex: 1, marginLeft: 16 }}>{ep.name}</span>
                {isActive ? <span className="badge context">Active Studio Context</span> : <span style={{ fontSize: 12, opacity: 0.5 }}>Set active</span>}
              </div>
            );
          })}
        </div>
        <div className="session-foot" style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24, alignItems: 'center' }}>
          <span className="sess"><span className="led" style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--good)', borderRadius: '50%', marginRight: 8 }}></span>Session active · {activeEp.code}</span>
          <button className="btn btn-primary" onClick={onNext}>Next: Source Media &rarr;</button>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   🎬 CENTRALIZED WORKSPACE DUBSTUDIO PORTAL
   -------------------------------------------------------------------------- */
window.Yanflix.DubStudio = function() {
  const [step, setStep] = useState(0);
  const [shows, setShows] = useState([
    { id: "smoking", title: "Smoking Behind the Supermarket With You", year: 2023, eps: [{ code: "S01E01", name: "The Usual Time" }] }
  ]);
  const [selectedShowId, setSelectedShowId] = useState(shows[0].id);
  const [episodes, setEpisodes] = useState(() => {
    const m = {}; shows.forEach(s => { m[s.id] = s.eps.slice(); }); return m;
  });
  const [activeEp, setActiveEp] = useState({ showId: shows[0].id, code: shows[0].eps[0].code });
  const [sceneId, setSceneId] = useState("sc01");
  
  const scene = (window.SCENES || [{ id: "sc01", name: "Scene 1", dur: 120 }]).find(s => s.id === sceneId);
  const [navRef, indStyle] = useIndicator(step, [step]);

  const activeShow = shows.find(s => s.id === activeEp.showId) || shows[0];

  const addSlot = (showId) => setEpisodes(prev => {
    const list = prev[showId] || [];
    const code = "S01E" + String(list.length + 1).padStart(2, "0");
    return { ...prev, [showId]: [...list, { code, name: "Untitled slot" }] };
  });

  const newProject = () => {
    const id = "proj" + Date.now();
    setShows(prev => [...prev, { id, title: "Untitled Project", year: new Date().getFullYear(), eps: [] }]);
    setEpisodes(prev => ({ ...prev, [id]: [] }));
    setSelectedShowId(id);
  };

  const AUDIO_STEPS = [2, 3, 5, 6];

  return (
    <div className="view fade-in">
      <div className="view-head" style={{ marginBottom: 20 }}>
        <div>
          <div className="eyebrow">Workspace</div>
          <h1 className="page-title" style={{ color: '#fff', fontSize: '38px', margin: 0 }}>Dub Studio</h1>
          <div className="sub" style={{ color: 'var(--ink-2)', fontSize: '14px', marginTop: 4 }}>{activeShow.title} &mdash; EN &rarr; ES · {activeEp.code}</div>
        </div>
      </div>

      <nav className="pipeline" ref={navRef} style={{ display: 'flex', position: 'relative', borderBottom: '1px solid var(--line)', marginBottom: 24, paddingBottom: 8 }}>
        {window.PIPELINE.map((s, i) => (
          <button key={s} data-tab className={"step" + (i === step ? " active" : "") + (i < step ? " done" : "")} onClick={() => setStep(i)} style={{ background: 'none', border: 'none', padding: '12px 16px', color: i === step ? '#fff' : 'var(--ink-3)', fontWeight: 600 }}>
            {s}
          </button>
        ))}
        <span className="indicator" style={{ ...indStyle, background: 'var(--crimson)', position: 'absolute', bottom: -1, height: 2 }}></span>
      </nav>

      {step === 0
        ? <ProjectPicker shows={shows} selectedShowId={selectedShowId} onSelectShow={setSelectedShowId}
            episodes={episodes} activeEp={activeEp} onSetActive={setActiveEp} onAddSlot={addSlot}
            onNewProject={newProject} onNext={() => setStep(1)} />
        : <div className="studio-grid" style={{ display: 'flex', gap: 20 }}>
            <div className="scenelist" style={{ width: 240, flexShrink: 0 }}>
              <div className="sl-head" style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: 8, borderBottom: '1px solid var(--line)' }}><span className="eyebrow">Scenes</span></div>
              {(window.SCENES || []).map((s, i) => (
                <div key={s.id} className={"scene-item" + (s.id === sceneId ? " active" : "")} onClick={() => setSceneId(s.id)} style={{ padding: 8, background: s.id === sceneId ? 'var(--bg-3)' : 'transparent', borderRadius: 4, cursor: 'pointer', marginTop: 4 }}>
                  <div className="sname" style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</div>
                </div>
              ))}
            </div>
            <div style={{ flex: 1 }}>
              {AUDIO_STEPS.includes(step) && <Waveform scene={scene} />}
              <StepPanel step={window.PIPELINE[step]} />
            </div>
          </div>}
    </div>
  );
};

/* --------------------------------------------------------------------------
   🚀 MAIN FRAMEWORK LAYOUT ROUTER
   -------------------------------------------------------------------------- */
function App() {
  const [view, setView] = useState("studio");
  
  const tabs = [
    { id: "studio", label: "Studio" },
    { id: "vault", label: "Character Vault" },
    { id: "library", label: "Library" },
    { id: "settings", label: "Settings" }
  ];
  
  const activeIndex = tabs.findIndex(t => t.id === view);
  const [navRef, indStyle] = useIndicator(activeIndex >= 0 ? activeIndex : 0, [view]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="wordmark">YANFLIX<span className="dot">.</span></span></div>
        <nav className="mainnav" ref={navRef}>
          {tabs.map(t => (
            <button key={t.id} data-tab className={"tab" + (t.id === view ? " active" : "")} onClick={() => setView(t.id)}>{t.label}</button>
          ))}
          <span className="indicator" style={indStyle}></span>
        </nav>
        <span className="spacer"></span>
        <div className="actions">
          <button className="iconbtn" title="Search"><svg viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round">{I.search}</svg></button>
          <button className="iconbtn" title="Notifications"><svg viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round">{I.bell}</svg></button>
          <div className="avatar">YN</div>
        </div>
      </header>

      <main className="main-content-area" style={{ padding: '32px 44px' }}>
        {view === "studio" && <window.Yanflix.DubStudio />}
        {view === "vault" && <window.Yanflix.VaultViewWrapper />}
        {view === "library" && <window.Yanflix.LibraryViewWrapper />}
        {view === "settings" && <div className="view"><h1 className="page-title">Settings</h1><p>Environment configurations active on local cache branches.</p></div>}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
