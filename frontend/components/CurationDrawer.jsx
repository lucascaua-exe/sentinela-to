const GAPS_POLL_MS = 4000;

function useKnowledgeGaps() {
  const [state, setState] = React.useState({ gaps: [], counts: {}, kb: null });
  const load = React.useCallback(
    () =>
      fetch("/api/v1/knowledge/gaps")
        .then((r) => (r.ok ? r.json() : Promise.reject(r)))
        .then(setState, () => {}),
    []
  );
  React.useEffect(() => {
    load();
    const timer = setInterval(load, GAPS_POLL_MS);
    return () => clearInterval(timer);
  }, [load]);
  return [state, load];
}

function timeAgo(epochSeconds) {
  const s = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
}

const linesOf = (text) => text.split("\n").map((l) => l.trim()).filter(Boolean);

function GapForm({ gap, onDone }) {
  const [cause, setCause] = React.useState(gap.hypothesis);
  const [immediate, setImmediate] = React.useState(gap.suggested_immediate.join("\n"));
  const [preventive, setPreventive] = React.useState(gap.suggested_preventive.join("\n"));
  const [norm, setNorm] = React.useState(gap.retrieved[0] || "");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    const resp = await fetch("/api/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gap_id: gap.id,
        correct_cause: cause,
        immediate_actions: linesOf(immediate),
        preventive_actions: linesOf(preventive),
        norm_reference: norm,
      }),
    }).catch(() => null);
    setBusy(false);
    if (resp && resp.ok) {
      const body = await resp.json();
      onDone(`Lacuna #${gap.id} validada · base v${body.kb_version} com ${body.chunks} trechos`);
      return;
    }
    if (resp && resp.status === 401) location.replace("/login");
    const detail = resp ? (await resp.json().catch(() => ({}))).detail : null;
    setError(
      typeof detail === "string" ? detail : "Revise os campos: causa com ao menos 10 caracteres e uma ação imediata."
    );
  };

  const dismiss = async () => {
    setBusy(true);
    const resp = await fetch(`/api/v1/knowledge/gaps/${gap.id}/dismiss`, { method: "POST" }).catch(() => null);
    setBusy(false);
    if (resp && resp.ok) onDone(`Lacuna #${gap.id} descartada`);
  };

  return (
    <form className="gap-form fade-in" onSubmit={submit}>
      <label className="field">
        <span>Causa confirmada</span>
        <textarea rows={3} value={cause} onChange={(e) => setCause(e.target.value)} maxLength={600} required />
      </label>
      <label className="field">
        <span>Ações imediatas <small>uma por linha</small></span>
        <textarea rows={2} value={immediate} onChange={(e) => setImmediate(e.target.value)} required />
      </label>
      <label className="field">
        <span>Ações preventivas <small>uma por linha</small></span>
        <textarea rows={2} value={preventive} onChange={(e) => setPreventive(e.target.value)} />
      </label>
      <label className="field">
        <span>Fundamentação normativa</span>
        <input value={norm} onChange={(e) => setNorm(e.target.value)} maxLength={160} placeholder="Ex.: NBR 5410 — proteção contra sobrecorrentes" />
      </label>
      {error && <p className="form-error">{error}</p>}
      <div className="gap-form-actions">
        <button type="button" className="btn-ghost" onClick={dismiss} disabled={busy}>
          Descartar
        </button>
        <button type="submit" className="btn-train" disabled={busy}>
          {busy ? "Indexando…" : "Validar e treinar"}
        </button>
      </div>
    </form>
  );
}

function GapItem({ gap, expanded, onToggle, onDone }) {
  return (
    <li className={`gap-item${expanded ? " is-open" : ""}`}>
      <button className="gap-summary" onClick={onToggle} aria-expanded={expanded}>
        <span className="gap-main">
          <strong>{CODE_LABELS[gap.signature] || gap.signature}</strong>
          <span className="gap-meta">
            #{gap.id} · {gap.city} · {timeAgo(gap.last_seen_at)}
            {gap.occurrences > 1 && ` · ${gap.occurrences} ocorrências`}
          </span>
        </span>
        <span className="gap-score">{formatPercent(gap.confidence)}</span>
      </button>
      {expanded && (
        <div className="gap-detail">
          {gap.measurement && <p className="gap-measure">Medição: {gap.measurement}</p>}
          <GapForm gap={gap} onDone={onDone} />
        </div>
      )}
    </li>
  );
}

function CurationDrawer({ open, data, onClose, onChanged }) {
  const [expanded, setExpanded] = React.useState(null);
  const [notice, setNotice] = React.useState("");

  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  React.useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(timer);
  }, [notice]);

  if (!open) return null;
  const { gaps, counts, kb } = data;
  const done = (message) => {
    setExpanded(null);
    setNotice(message);
    onChanged();
  };

  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="curation-title">
        <header className="drawer-head">
          <div>
            <h2 id="curation-title">Curadoria da base</h2>
            <p>Diagnósticos abaixo de {kb ? kb.threshold : 90}% aguardando validação da engenharia.</p>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Fechar">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" />
            </svg>
          </button>
        </header>

        {kb && (
          <div className="kb-stats">
            <span><b>v{kb.version}</b> versão da base</span>
            <span><b>{kb.chunks}</b> trechos indexados</span>
            <span><b>{counts.validated || 0}</b> validados</span>
          </div>
        )}

        {notice && <p className="drawer-notice fade-in" role="status">{notice}</p>}

        {gaps.length === 0 ? (
          <div className="drawer-empty">
            <strong>Nenhuma lacuna aberta</strong>
            <span>Quando a confiança de um diagnóstico ficar abaixo do limiar, ele aparece aqui para validação.</span>
          </div>
        ) : (
          <ul className="gap-list">
            {gaps.map((gap) => (
              <GapItem
                key={gap.id}
                gap={gap}
                expanded={expanded === gap.id}
                onToggle={() => setExpanded(expanded === gap.id ? null : gap.id)}
                onDone={done}
              />
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
