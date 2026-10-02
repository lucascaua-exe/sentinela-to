const HEADER_STATUS = {
  off: "Offline",
  success: "Operando",
  critical: "Em alerta",
};

const NAV_ITEMS = [
  { id: "panel", label: "Painel", hash: "#/" },
  { id: "history", label: "Histórico e relatórios", hash: "#/historico" },
];

function Header({ live, critical, user, onLogout, sites, siteId, onSiteChange, openGaps, onOpenCuration, view, onOpenSettings }) {
  const tone = !live ? "off" : critical ? "critical" : "success";
  return (
    <header className="header">
      <div className="header-left">
        <div className="brand">
          <span className="brand-mark" role="img" aria-label="Logo Sentinela TO" />
          <span className="brand-name">
            Sentinela<strong>TO</strong>
          </span>
        </div>
        <nav className="main-nav" aria-label="Seções">
          {NAV_ITEMS.map((item) => (
            <a key={item.id} href={item.hash} className={view === item.id ? "is-active" : ""} aria-current={view === item.id ? "page" : undefined}>
              {item.label}
            </a>
          ))}
        </nav>
      </div>
      <div className="header-actions">
        {view === "panel" && (
          <SiteSelector
            sites={sites}
            siteId={siteId}
            onChange={onSiteChange}
            tone={tone}
            statusLabel={HEADER_STATUS[tone]}
          />
        )}
        <button
          className={`btn-curation${openGaps ? " has-gaps" : ""}`}
          onClick={onOpenCuration}
          title="Curadoria da base de conhecimento"
        >
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 4.5h8.5a3 3 0 0 1 3 3V16H7a3 3 0 0 1-3-3V4.5z" />
            <path d="M7.5 8.5h5M7.5 11.5h3" />
          </svg>
          Curadoria
          {openGaps > 0 && <span className="gap-badge">{openGaps}</span>}
        </button>
        <button className="btn-icon" onClick={onOpenSettings} title="Configurações" aria-label="Configurações">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
        {user && (
          <span className="user-chip">
            <span className="avatar" title={user} aria-label={`Usuário ${user}`}>
              {user.charAt(0).toUpperCase()}
            </span>
            <button className="btn-ghost btn-logout" onClick={onLogout}>Sair</button>
          </span>
        )}
      </div>
    </header>
  );
}
