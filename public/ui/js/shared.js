/* ============ shared.js — icons, data, reusable components ============ */
const { useState, useEffect, useRef, useLayoutEffect, useCallback } = React;

// Anchor explicit Yanflix registry onto global window context safely
window.Yanflix = window.Yanflix || {};

window.Yanflix.Ico = {
  heart: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>,
  eye: <g><path d="M1 12s4-7.5 11-7.5S23 12 23 12s-4 7.5-11 7.5S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></g>,
  search: <g><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></g>,
  bell: <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>,
  plus: <g><path d="M12 5v14M5 12h14"/></g>,
};

window.I = window.Yanflix.Ico;

window.Star = function(props) {
  return <svg viewBox="0 0 24 24" {...props}><path d="M12 2l2.95 6.06 6.55.95-4.75 4.62 1.12 6.52L12 17.6 6.13 20.7l1.12-6.52L2.5 9.01l6.55-.95z"/></svg>;
};

window.LANGS = ["All", "Spanish", "French", "Japanese", "German", "Portuguese", "Korean", "Hindi"];

window.LIBRARY = [
  { id: "aurora",   title: "Aurora Protocol",     year: 2025, dub: "Spanish",    rating: "8.4", status: "done",       fav: true  },
  { id: "nightcity",title: "Night City Rain",     year: 2024, dub: "Japanese",   rating: "9.1", status: "done",       fav: false },
  { id: "redline",  title: "Redline",             year: 2025, dub: "French",     rating: "7.6", status: "done",       fav: false },
  { id: "saltflats",title: "The Salt Flats",      year: 2023, dub: "German",     rating: "8.0", status: "done",       fav: true  },
  { id: "lastorbit",title: "Last Orbit",          year: 2025, dub: "Korean",     rating: "8.8", status: "done",       fav: false },
  { id: "verdant",  title: "Verdant",             year: 2024, dub: "Portuguese", rating: "7.2", status: "done",       fav: false },
  { id: "ghostkit", title: "Ghost Kitchen",       year: 2025, dub: "Spanish",    rating: "6.9", status: "processing", fav: false },
  { id: "monsoon",  title: "Monsoon Season 2",    year: 2024, dub: "Hindi",      rating: "8.3", status: "done",       fav: true  },
  { id: "driftwood",title: "Driftwood",           year: 2023, dub: "French",     rating: "7.8", status: "done",       fav: false },
  { id: "blackbox", title: "Black Box",           year: 2025, dub: "German",     rating: "8.6", status: "queued",     fav: false },
  { id: "tundra",   title: "Tundra",              year: 2024, dub: "Japanese",   rating: "7.4", status: "done",       fav: false },
  { id: "neonalibi",title: "Neon Alibi",          year: 2025, dub: "Korean",     rating: "9.0", status: "done",       fav: true  },
];

window.ACTORS = [
  { id: "mara",   name: "Mara Voss",      role: "Lead · Dramatic",   langs: ["ES","FR"],      tags: ["Warm", "Alto"],       lines: 412, rating: "9.2", fav: true  },
  { id: "kenji",  name: "Kenji Arai",     role: "Lead · Action",     langs: ["JA","KO"],      tags: ["Gravel", "Baritone"], lines: 388, rating: "8.9", fav: false },
  { id: "lena",   name: "Lena Hartmann",  role: "Support · Comedy",  langs: ["DE","EN"],      tags: ["Bright", "Soprano"],  lines: 276, rating: "8.5", fav: false },
  { id: "diego",  name: "Diego Salas",    role: "Lead · Romance",    langs: ["ES","PT"],      tags: ["Smooth", "Tenor"],    lines: 521, rating: "9.4", fav: true  },
  { id: "yara",   name: "Yara Okonkwo",   role: "Narrator",          langs: ["EN","FR"],      tags: ["Rich", "Contralto"],  lines: 198, rating: "8.7", fav: false },
  { id: "soomin", name: "Soo-min Park",   role: "Support · Thriller",langs: ["KO","JA"],      tags: ["Crisp", "Mezzo"],     lines: 304, rating: "8.2", fav: false },
  { id: "amir",   name: "Amir Haddad",    role: "Lead · Epic",       langs: ["HI","EN"],      tags: ["Deep", "Bass"],       lines: 357, rating: "9.0", fav: true  },
];

window.PIPELINE = ["Project", "Source Media", "Vocal Isolation", "Speakers", "Script", "Voices", "Mixer", "Export"];

window.SCENES = [
  { id: "sc01", name: "Cold open — rooftop", line: "You shouldn't have come back.", dur: 134 },
  { id: "sc02", name: "Interrogation", line: "Tell me where the box is.", dur: 198 },
  { id: "sc03", name: "Market chase", line: "Move! Out of the way!", dur: 87 },
  { id: "sc04", name: "Hospital", line: "She's stable. For now.", dur: 156 },
  { id: "sc05", name: "Final confrontation", line: "It was always you.", dur: 221 },
];

window.SCRIPT = [
  { who: "Mara Voss",  vc: "ES · Warm",     tc: "00:04 — 00:09", src: "You shouldn't have come back.",   dub: "No deberías haber vuelto." },
  { who: "Kenji Arai", vc: "ES · Gravel",   tc: "00:09 — 00:13", src: "I didn't have a choice.",          dub: "No tuve elección." },
  { who: "Mara Voss",  vc: "ES · Warm",     tc: "00:14 — 00:19", src: "There's always a choice.",         dub: "Siempre hay una elección." },
  { who: "Diego Salas",vc: "ES · Smooth",   tc: "00:20 — 00:26", src: "Then I choose you.",               dub: "Entonces te elijo a ti." },
];

window.useIndicator = function(activeIndex, deps) {
  const containerRef = useRef(null);
  const [style, setStyle] = useState({ width: 0, transform: "translateX(0)" });
  useLayoutEffect(() => {
    const c = containerRef.current;
    if (!c) return;
    const items = c.querySelectorAll("[data-tab]");
    const el = items[activeIndex];
    if (!el) return;
    setStyle({ width: el.offsetWidth + "px", transform: "translateX(" + el.offsetLeft + "px)" });
  }, [activeIndex, deps]);
  return [containerRef, style];
};

window.FilterPills = function({ options, value, onChange, single }) {
  const toggle = (opt) => {
    if (single) { onChange(opt); return; }
    if (opt === "All") { onChange(["All"]); return; }
    let next = value.includes(opt) ? value.filter(v => v !== opt) : [...value.filter(v => v !== "All"), opt];
    if (next.length === 0) next = ["All"];
    onChange(next);
  };
  const isOn = (opt) => single ? value === opt : value.includes(opt);
  return (
    <div className="pill-row">
      {options.map(opt => (
        <button key={opt} className={"pill" + (isOn(opt) ? " selected" : "")} onClick={() => toggle(opt)}>{opt}</button>
      ))}
    </div>
  );
};

window.VideoCard = function({ m }) {
  const [fav, setFav] = useState(m.fav);
  const [viewed, setViewed] = useState(false);
  const statusLabel = { done: "Complete", processing: "Dubbing", queued: "Queued" }[m.status];
  return (
    <div className="card fade-in">
      <div className="poster" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
         <span style={{ fontSize: '32px', opacity: 0.15 }}>🎬</span>
      </div>
      <div className="title" style={{ marginTop: 12, fontWeight: 600 }}>{m.title}</div>
      <div className="meta">
        <span className="year">{m.year}</span>
        <button className={"icon-btn fav" + (fav ? " on" : "")} onClick={() => setFav(f => !f)} title="Favorite">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{window.Yanflix.Ico.heart}</svg>
        </button>
        <span className="spacer"></span>
        <span className="rating"><window.Star style={{ width: 14, height: 14, display: 'inline', marginRight: 4 }} /> {m.rating}</span>
      </div>
      <div className="langline" style={{ marginTop: 8 }}>
        <span className="btn btn-small btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }}>{m.dub}</span>
      </div>
    </div>
  );
};

window.ActorCard = function({ a }) {
  const [fav, setFav] = useState(a.fav);
  return (
    <div className="actor fade-in">
      <div className="name" style={{ fontWeight: 700, fontSize: '16px' }}>{a.name}</div>
      <div className="role" style={{ color: 'var(--muted-2)', fontSize: '12px', marginTop: 4 }}>{a.role}</div>
      <div className="stats" style={{ display: 'flex', gap: 16, marginTop: 12 }}>
        <div className="stat"><span className="n">{a.lines}</span><span className="l" style={{ fontSize: '10px', color: 'var(--muted)' }}> Lines</span></div>
        <div className="stat"><span className="n gold" style={{ color: 'var(--gold)' }}>{a.rating}</span><span className="l" style={{ fontSize: '10px', color: 'var(--muted)' }}> Quality</span></div>
      </div>
    </div>
  );
};
