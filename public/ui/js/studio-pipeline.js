const { Ico, fmtTime, fmtSize, slugify, waveHeights, AnimWaveform, apiPost, apiGet, startLogPolling } = window.Yanflix;

window.Yanflix.LockBar = function({ locked, lockedText, idleText, actionLabel, onAction, onContinue, continueLabel, regen, onRegen }) {
  return (
    <div className="lock-bar">
      <div className="lock-note">
        <span className={'dot' + (locked ? ' ok' : '')}></span>
        <span>{locked ? (lockedText || 'Stage locked') : (idleText || 'Pending action')}</span>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        {actionLabel && onAction && (
          <button className="btn btn-primary btn-small" onClick={onAction} disabled={locked}>
            {actionLabel}
          </button>
        )}
        {regen && locked && onRegen && (
          <button className="btn btn-small" onClick={onRegen}>Regenerate</button>
        )}
        {onContinue && (
          <button className="btn btn-gold btn-small" onClick={onContinue}>{continueLabel || 'Continue →'}</button>
        )}
      </div>
    </div>
  );
};

window.Yanflix.StageNav = function({ go, prev, next, nextLabel, status, locked, regen, onRegen, extra }) {
  return (
    <div className="lock-bar">
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {prev ? <button className="btn btn-small btn-ghost" onClick={() => go(prev)}>← Back</button> : <span></span>}
        {status && <span className="lock-note"><span className="dot ok"></span>{status}</span>}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        {extra}
        {regen && <button className="btn btn-small" onClick={onRegen}><Ico.regen width="13" height="13" style={{ marginRight: 6, verticalAlign: -2 }}/>Clear Track</button>}
        {next && <button className="btn btn-gold btn-small" onClick={() => go(next)} disabled={!locked}>{nextLabel || 'Next →'}</button>}
      </div>
    </div>
  );
};

window.Yanflix.SubProject = function({ job, setJob, upd, projects, setProjects, pushToast, go }) {
  const [creating, setCreating] = React.useState(false);
  const [addingEpisode, setAddingEpisode] = React.useState(false);
  
  // Form States
  const [title, setTitle] = React.useState('');
  const [format, setFormat] = React.useState('Series');
  const [genre, setGenre] = React.useState('');
  const [year, setYear] = React.useState('');
  
  const [epLabel, setEpLabel] = React.useState('S01E01');
  const [epName, setEpName] = React.useState('The Usual Time');

  const activeProject = projects.find(p => p.id === job.projectId);

  // Initialize with your true project tracking history asset context on initial launch
  React.useEffect(() => {
    if (projects.length === 0) {
      const initialRoster = [
        {
          id: 'p_smoking',
          title: 'Smoking Behind the Supermarket With You',
          format: 'Series',
          genres: ['Slice of Life', 'Romance'],
          year: '2023',
          slug: 'smoking_behind_the_supermarket_with_you',
          thumb: '/ui/img/smoking_btswy_thumbnail.png',
          episodes: [
            { id: 'e1', label: 'S01E01', name: 'The Usual Time', status: 'empty' }
          ],
        }
      ];
      setProjects(initialRoster);
      if (!job.projectId) {
        setJob(window.Yanflix.freshJob(initialRoster[0], initialRoster[0].episodes[0]));
      }
    }
  }, []);

  const selectEpisode = (proj, ep) => {
    if (ep.status === 'complete') {
      pushToast({ kind: 'info', title: 'Already dubbed', msg: `${ep.label} is complete.` });
      return;
    }
    setJob(window.Yanflix.freshJob(proj, ep));
    pushToast({ kind: 'success', title: 'Studio Target Synced', msg: `${proj.title} · ${ep.label}` });
  };

  const createProject = () => {
    if (!title.trim()) { pushToast({ kind: 'error', title: 'Title required' }); return; }
    const slug = slugify(title);
    const firstEp = { id: 'ep_' + Date.now(), label: format === 'Movie' ? 'MOVIE' : 'S01E01', name: format === 'Movie' ? title.trim() : 'Episode 1', status: 'empty' };
    const proj = { id: 'p_' + Date.now(), title: title.trim(), format, genres: genre ? genre.split(',').map(g => g.trim()).filter(Boolean) : [], year, slug, thumb: null, episodes: [firstEp] };
    
    setProjects(p => [proj, ...p]);
    setJob(window.Yanflix.freshJob(proj, firstEp));
    pushToast({ kind: 'success', title: 'Project Spawned', msg: `${title} initialized.` });
    setCreating(false); setTitle(''); setGenre(''); setYear('');
  };

  const addCustomEpisode = () => {
    if (!epLabel.trim() || !epName.trim()) { pushToast({ kind: 'error', title: 'All fields required' }); return; }
    
    const newEp = { id: 'ep_' + Date.now(), label: epLabel.trim().toUpperCase(), name: epName.trim(), status: 'empty' };
    
    setProjects(prev => prev.map(p => {
      if (p.id === job.projectId) {
        // Prevent duplicate labels inside the same series container
        if (p.episodes.some(e => e.label.toUpperCase() === epLabel.trim().toUpperCase())) {
          pushToast({ kind: 'error', title: 'Label Collision', msg: `Label ${epLabel} already maps to an existing file slot.` });
          return p;
        }
        const updatedEps = [...p.episodes, newEp];
        return { ...p, episodes: updatedEps };
      }
      return p;
    }));

    setAddingEpisode(false);
    setEpLabel('');
    setEpName('');
    pushToast({ kind: 'success', title: 'Episode Appended', msg: `Added slot ${epLabel} successfully.` });
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', marginBottom: 20 }}>

        {/* LEFT — project library */}
        <div className="panel" style={{ width: 260, flexShrink: 0 }}>
          <div className="panel-title" style={{ marginBottom: 6 }}>Movies &amp; Episodes</div>
          <div className="help" style={{ marginBottom: 14, color: 'var(--ink-2)', fontSize: 12 }}>
            Select your pipeline show container.
          </div>
          <div style={{ marginBottom: 16 }}>
            <button className="btn btn-primary btn-small" onClick={() => { setCreating(c => !c); setAddingEpisode(false); }}>
              <Ico.plus width="13" height="13" style={{ marginRight: 6, verticalAlign: -2 }}/>New Project
            </button>
          </div>

          {creating && (
            <div style={{ background: 'var(--bg-2)', border: '1px solid var(--line-2)', borderRadius: 6, padding: 14, marginBottom: 16 }}>
              <div style={{ marginBottom: 10 }}><div className="label">Title</div><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Show title" style={{ background: 'var(--bg-1)' }} /></div>
              <div style={{ marginBottom: 10 }}><div className="label">Format</div><select className="select" value={format} onChange={(e) => setFormat(e.target.value)} style={{ background: 'var(--bg-1)' }}><option>Series</option><option>Movie</option></select></div>
              <div style={{ marginBottom: 10 }}><div className="label">Genres</div><input className="input" value={genre} onChange={(e) => setGenre(e.target.value)} placeholder="Slice of Life, Romance" style={{ background: 'var(--bg-1)' }} /></div>
              <div style={{ marginBottom: 14 }}><div className="label">Year</div><input className="input" value={year} onChange={(e) => setYear(e.target.value)} placeholder="2023" style={{ background: 'var(--bg-1)' }} /></div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-primary btn-small" onClick={createProject}>Create</button>
                <button className="btn btn-ghost btn-small" onClick={() => setCreating(false)}>Cancel</button>
              </div>
            </div>
          )}

          <div className="media-grid" style={{ gridTemplateColumns: '1fr' }}>
            {projects.map(p => (
              <div key={p.id} className="media-card" onClick={() => selectEpisode(p, p.episodes[0])} style={{ outline: p.id === job.projectId ? '2px solid var(--gold)' : 'none', outlineOffset: 3, borderRadius: 6 }}>
                <image-slot
                  id={"poster-" + p.id}
                  shape="rounded"
                  radius="6"
                  src={p.thumb || ''}
                  placeholder={p.title}
                  style={{ width: '100%', aspectRatio: '2/3', display: 'block' }}
                ></image-slot>
                <p className="card-title">{p.title}</p>
                <div className="card-meta">
                  <span>{p.year || p.format}</span>
                  <span className="spacer"></span>
                  <span className="card-rating">
                    <svg viewBox="0 0 24 24"><path d="M12 2l2.95 6.06 6.55.95-4.75 4.62 1.12 6.52L12 17.6 6.13 20.7l1.12-6.52L2.5 9.01l6.55-.95z"/></svg>
                    {p.episodes.length} ep
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT — episodes for selected project */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {activeProject ? (
            <div className="panel">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div className="panel-title">{activeProject.title} — Active Seasons</div>
                <button className="btn btn-small" style={{ borderColor: 'var(--gold)', color: 'var(--gold)' }} onClick={() => { setAddingEpisode(a => !a); setCreating(false); }}>
                  + Add Episode Slot
                </button>
              </div>

              {addingEpisode && (
                <div style={{ background: 'var(--bg-2)', border: '1px solid var(--line-2)', borderRadius: 6, padding: 14, marginBottom: 16 }}>
                  <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                    <div style={{ flex: 1 }}><div className="label">Episode Code</div><input className="input" value={epLabel} onChange={(e) => setEpLabel(e.target.value)} placeholder="S01E02" style={{ background: 'var(--bg-1)' }} /></div>
                    <div style={{ flex: 2 }}><div className="label">Episode Title</div><input className="input" value={epName} onChange={(e) => setEpName(e.target.value)} placeholder="e.g. Behind the Store" style={{ background: 'var(--bg-1)' }} /></div>
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-gold btn-small" onClick={addCustomEpisode}>Commit Track Slot</button>
                    <button className="btn btn-ghost btn-small" onClick={() => setAddingEpisode(false)}>Cancel</button>
                  </div>
                </div>
              )}

              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 480, overflowY: 'auto' }}>
                {activeProject.episodes.map(ep => (
                  <div key={ep.id} className={'ep-row' + (ep.id === job.episodeId ? ' active' : '')} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 14 }}>
                    <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--gold)', fontSize: 12, minWidth: 60 }}>{ep.label}</span>
                    <div style={{ color: '#fff', flex: 1, fontSize: 14 }}>{ep.name}</div>
                    <button className="btn btn-small btn-primary" onClick={() => selectEpisode(activeProject, ep)} disabled={ep.id === job.episodeId}>
                      {ep.id === job.episodeId ? 'Active Studio Context' : 'Load Profile'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="panel" style={{ color: 'var(--ink-3)', fontSize: 13, padding: 32, textAlign: 'center' }}>
              Select a project to view its episodes.
            </div>
          )}
        </div>

      </div>

      <window.Yanflix.StageNav go={go} next="source" nextLabel="Next: Source Media →" status={job.episodeId ? `Session active · ${job.episodeLabel}` : 'Pick an episode to begin'} locked={!!job.episodeId} />
    </>
  );
};

window.Yanflix.SubSource = function({ job, upd, pushToast, go }) {
  const inputRef = React.useRef(null);
  const [scanning, setScanning] = React.useState(false);
  const epFolder = job.episodeFolder || slugify(job.episodeLabel || job.episodeName);

  React.useEffect(() => {
    if (!job.episodeId || job.media) return;
    setScanning(true);
    apiGet(`/api/status?ep_folder=${encodeURIComponent(epFolder)}`)
      .then(res => {
        if (res && res.filename) {
          upd({
            media: { name: res.filename, duration: 'Detected', size: 'Local Cache', tracks: 1 },
            episodeFolder: epFolder
          });
          pushToast({ kind: 'success', title: 'Auto-Detected Existing Track', msg: `Found: ${res.filename}` });
        }
      })
      .catch(() => {})
      .finally(() => setScanning(false));
  }, [job.episodeId]);

  const handleUpload = async (e) => {
    const f = e.target.files?.[0]; if (!f) return;
    upd({
      media: { name: f.name, duration: 'Calculating', size: (f.size / 1000000).toFixed(1) + ' MB', tracks: 1 },
      episodeFolder: epFolder
    });
    const form = new FormData();
    form.append('file', f);
    try {
      const res = await fetch('/api/upload-source', { method: 'POST', body: form });
      if (!res.ok) throw new Error("Upload error");
      pushToast({ kind: 'success', title: 'File saved directly to disk' });
    } catch (err) {
      pushToast({ kind: 'error', title: 'Upload failed' });
    }
  };

  if (!job.episodeId) return <window.Yanflix.NeedEpisode go={go} />;

  return (
    <>
      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="stage-head">
          <div className="panel-title">Source Media Assignment</div>
          {scanning && <span style={{ color: 'var(--gold)', fontSize: '12px' }}>Auto-scanning directory cache...</span>}
        </div>
        
        {!job.media ? (
          <div className="media-opts">
            <div className="media-opt" style={{ flex: 1, padding: 32, border: '1px dashed var(--line-2)', borderRadius: 6, textAlign: 'center', cursor: 'pointer' }} onClick={() => inputRef.current?.click()}>
              <input ref={inputRef} type="file" accept="audio/*,video/*" hidden onChange={handleUpload} />
              <div className="glyph" style={{ margin: '0 auto 12px', width: 44, height: 44, borderRadius: '50%', background: 'var(--bg-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--gold)' }}><Ico.upload width="16" height="16"/></div>
              <div className="heading">Drop or Select Show Track</div>
              <div className="sub" style={{ color: 'var(--ink-3)', fontSize: 12, marginTop: 4 }}>No file needed if already present in dynamic folders.</div>
            </div>
          </div>
        ) : (
          <div style={{ padding: '16px', background: 'rgba(74,222,128,0.04)', border: '1px solid var(--good)', borderRadius: 6 }}>
            <div style={{ fontSize: '14px', fontWeight: 500, color: '#fff' }}>🎯 Linked Asset: <code style={{ color: 'var(--gold)' }}>{job.media.name}</code></div>
          </div>
        )}
      </div>
      <window.Yanflix.StageNav go={go} prev="project" next="isolate" nextLabel="Next: Vocal Isolation →" status={job.media ? "Asset verified on disk" : "Awaiting asset hook"} locked={!!job.media} regen={!!job.media} onRegen={() => upd({ media: null })} />
    </>
  );
};

window.Yanflix.NeedEpisode = function({ go }) {
  return (
    <div className="empty" style={{ padding: '48px 0' }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>🎬</div>
      <div style={{ fontSize: 15, color: 'var(--ink-2)' }}>No active episode targets loaded.</div>
      <button className="btn btn-primary btn-small" style={{ marginTop: 12 }} onClick={() => go('project')}>Go to Project Selection</button>
    </div>
  );
};

window.Yanflix.SubIsolate = function({ job, upd, pushToast, go }) {
  return (
    <div className="panel">
      <div className="panel-title">Stage 3 · Vocal Isolation (Demucs Stems)</div>
      <p className="page-sub" style={{ marginBottom: 16 }}>Decouple original vocal stems directly from music soundtracks and environmental sound effects layouts.</p>
      <window.Yanflix.LockBar locked={false} idleText="Demucs workflow module ready" actionLabel="Separate Vocals" onContinue={() => go('speakers')} />
    </div>
  );
};

window.Yanflix.SubVoices = function({ job, upd, characters, pushToast, go }) {
  return (
    <div className="panel">
      <div className="stage-head">
        <div className="panel-title">Stage 6 · Actor Inference (IndexTTS-2 Synthesis)</div>
        <span className="badge pending">0 Lines Generated</span>
      </div>
      <p className="page-sub" style={{ marginBottom: 16 }}>Synthesize directed target translation strings into native high-fidelity wave assets matching assigned voice profile clones.</p>
      <window.Yanflix.LockBar locked={false} idleText="IndexTTS Voice Generator standby" actionLabel="Generate Audio Tracks" onContinue={() => go('mixer')} />
    </div>
  );
};

window.Yanflix.SubMixer = function({ job, upd, go }) {
  return (
    <div className="panel">
      <div className="panel-title">Stage 7 · Audio Stem Mixer Controls</div>
      <p className="page-sub" style={{ marginBottom: 16 }}>Optimize relative volume balances between synthesized text overlays and extracted background sound effects profiles.</p>
      <window.Yanflix.LockBar locked={true} lockedText="Hardware mix matrices ready" onContinue={() => go('export')} />
    </div>
  );
};

window.Yanflix.SubExport = function({ job, upd, setProjects, pushToast, stageDone }) {
  return (
    <div className="panel">
      <div className="panel-title">Stage 8 · Sync &amp; Mux Final Export Compilation</div>
      <p className="page-sub" style={{ marginBottom: 16 }}>Assemble timed voice spans over original background media streams and compile into hardware-accelerated MP4 container spaces.</p>
      <button className="btn btn-primary" onClick={() => pushToast({ kind: 'success', title: 'Export sequence mapped' })}>Export Final Dub</button>
    </div>
  );
};
