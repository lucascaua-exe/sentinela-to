function plainHeadline(text) {
  return (text || "").replace(/^(ALERTA CRÍTICO|ATENÇÃO):\s*/, "");
}

function transferView(ats, sample) {
  if (ats && ats.state === "ARMED") {
    const total = ats.countdown_total_s || 1;
    const left = ats.countdown_s == null ? total : ats.countdown_s;
    return {
      tone: "critical",
      title: `Comutação para autoprodução em ${Math.ceil(left)} s`,
      detail: plainHeadline(ats.pending_reason),
      progress: left / total,
      actions: [
        { id: "cancel", label: "Cancelar" },
        { id: "force", label: "Comutar agora", primary: true },
      ],
    };
  }
  if (ats && ats.state === "BACKUP") {
    const parts = [`Bateria ${Math.round(ats.battery_soc)}%`];
    if (ats.load_shed) parts.push("cargas não críticas desligadas");
    if (ats.restore_in_s != null) parts.push(`retorno à rede em ${Math.ceil(ats.restore_in_s)} s`);
    return {
      tone: "backup",
      title: "Planta em autoprodução · Solar + BESS",
      detail: parts.join(" · "),
      actions: [{ id: "restore", label: "Retornar à rede" }],
    };
  }
  if (ats && ats.state === "RESTORING") {
    return { tone: "backup", title: "Sincronizando retorno à rede", detail: "Aguardando estabilidade da concessionária." };
  }
  const severity = sample && sample.analysis.severity;
  if (severity === "CRITICAL" || severity === "WARNING") {
    return {
      tone: severity === "CRITICAL" ? "critical" : "warning",
      title: plainHeadline(sample.analysis.headline),
      detail: ats && ats.rearm_blocked ? "Comutação automática suspensa pelo operador até a rede normalizar." : sample.analysis.recommended_action,
    };
  }
  return null;
}

function TransferBar({ ats, sample, onCommand }) {
  const view = transferView(ats, sample);
  if (!view) return null;
  return (
    <section className={`card transfer-bar tone-${view.tone}`} role="status" aria-live="polite">
      <div className="transfer-text">
        <strong>{view.title}</strong>
        {view.detail && <span>{view.detail}</span>}
      </div>
      {view.actions && (
        <div className="transfer-actions">
          {view.actions.map((a) => (
            <button key={a.id} className={a.primary ? "btn-danger" : "btn-ghost"} onClick={() => onCommand(a.id)}>
              {a.label}
            </button>
          ))}
        </div>
      )}
      {view.progress != null && (
        <i className="transfer-progress" style={{ transform: `scaleX(${Math.max(0, Math.min(1, view.progress))})` }} />
      )}
    </section>
  );
}
