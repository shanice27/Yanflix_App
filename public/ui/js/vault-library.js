window.Yanflix = window.Yanflix || {};

window.Yanflix.VaultViewWrapper = function() {
  return (
    <div className="view fade-in">
      <div className="view-head">
        <div>
          <div className="eyebrow">Voice Roster</div>
          <h1 className="page-title">Character Vault</h1>
          <div className="sub">Global clone indexer and show roster assignments.</div>
        </div>
      </div>
      <div className="vault-grid">
        {window.ACTORS.map(a => <window.ActorCard key={a.id} a={a} />)}
      </div>
    </div>
  );
};

window.Yanflix.LibraryViewWrapper = function() {
  return (
    <div className="view fade-in">
      <div className="view-head">
        <div>
          <div className="eyebrow">Archive</div>
          <h1 className="page-title">Suite Library</h1>
          <div className="sub">Every show is a shelf of episode posters.</div>
        </div>
      </div>
      <div className="grid">
        {window.LIBRARY.map(m => <window.VideoCard key={m.id} m={m} />)}
      </div>
    </div>
  );
};
