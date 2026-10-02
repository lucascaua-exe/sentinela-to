function siteTone(site) {
  if (!site.online) return { tone: "off", text: "Sem telemetria" };
  if (site.severity === "CRITICAL") return { tone: "critical", text: "Em alerta" };
  if (site.severity === "WARNING") return { tone: "warning", text: "Atenção" };
  return { tone: "success", text: "Operando" };
}

function SiteSelector({ sites, siteId, onChange, tone, statusLabel }) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef(null);
  const current = sites.find((s) => s.id === siteId);
  const label = current ? `${current.city} · ${current.unit}` : "Carregando…";

  React.useEffect(() => {
    if (!open) return;
    const onPointer = (e) => rootRef.current && !rootRef.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (id) => {
    setOpen(false);
    if (id !== siteId) onChange(id);
  };

  return (
    <div className="site-selector" ref={rootRef}>
      <button
        className="site-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Unidade: ${label}, ${statusLabel}. Alterar unidade`}
        title={statusLabel}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`status-dot tone-${tone}`} aria-hidden="true" />
        <span>{label}</span>
        <svg className="site-chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <ul className="site-menu" role="listbox" aria-label="Unidades monitoradas">
          <li className="site-menu-title" role="presentation">Unidades monitoradas</li>
          {sites.map((site) => {
            const status = siteTone(site);
            const selected = site.id === siteId;
            return (
              <li key={site.id} role="option" aria-selected={selected}>
                <button className={`site-option ${selected ? "is-selected" : ""}`} onClick={() => choose(site.id)}>
                  <span className={`status-dot tone-${status.tone}`} aria-hidden="true" />
                  <span className="site-option-text">
                    <span className="site-option-city">{site.city}</span>
                    <span className="site-option-meta">
                      {site.unit} · {status.text}
                    </span>
                  </span>
                  {selected && (
                    <svg className="site-check" viewBox="0 0 16 16" aria-hidden="true">
                      <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
