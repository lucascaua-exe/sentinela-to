const SEVERITY_TONE = { "CRÍTICA": "critical", ALTA: "warning", "MÉDIA": "warning", BAIXA: "info" };
const SEVERITY_TEXT = { "CRÍTICA": "Crítico", ALTA: "Alto", "MÉDIA": "Moderado", BAIXA: "Baixo" };
const ACTION_GROUPS = [
  { type: "IMEDIATA", title: "Agora" },
  { type: "PREVENTIVA", title: "Prevenção" },
];

function groupedRefs(refs) {
  const groups = new Map();
  refs.forEach((ref) => {
    const source = ref.split(" — ")[0];
    groups.set(source, [...(groups.get(source) || []), ref]);
  });
  return [...groups].map(([source, items]) => ({
    source,
    detail: items.join("\n"),
    validated: source.startsWith("Ocorrência validada"),
  }));
}

function confidenceTooltip(diagnosis) {
  const b = diagnosis.confidence_breakdown;
  if (!b) return "";
  const fmt = (v) => v.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
  return `CS = 0,7 × similaridade vetorial (${fmt(b.vector)}) + 0,3 × densidade de evidência (${fmt(b.density)})`;
}

function causeParts(diagnosis) {
  const prefix = diagnosis.measurement ? `Medição: ${diagnosis.measurement}. ` : "";
  const cause = prefix && diagnosis.cause_analysis.startsWith(prefix)
    ? diagnosis.cause_analysis.slice(prefix.length)
    : diagnosis.cause_analysis;
  const [headline, ...rest] = cause.split(/(?<=\.)\s+(?=[A-ZÀ-Ú])/);
  return { headline, detail: rest.join(" ") };
}

function measurementItems(measurement) {
  return (measurement || "")
    .split("; ")
    .filter(Boolean)
    .map((item) => item.charAt(0).toUpperCase() + item.slice(1));
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path d="M10 2.5l1.6 4.4 4.4 1.6-4.4 1.6L10 14.5l-1.6-4.4L4 8.5l4.4-1.6L10 2.5z" />
      <path d="M15.5 12.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" opacity="0.6" />
    </svg>
  );
}

function VoiceButton({ speaking, onClick }) {
  return (
    <button className={`btn-voice${speaking ? " is-speaking" : ""}`} aria-pressed={speaking} onClick={onClick}>
      {speaking ? (
        <span className="voice-bars" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </span>
      ) : (
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 8v4h3l4 3V5L6 8H3z" />
          <path d="M13.5 7.5a3.5 3.5 0 0 1 0 5M15.5 5a7 7 0 0 1 0 10" />
        </svg>
      )}
      {speaking ? "Parar" : "Ouvir"}
    </button>
  );
}

function shouldNarrate(diagnosis, settings) {
  if (!settings.auto_voice) return false;
  if (diagnosis.severity_level === "CRÍTICA") return true;
  return settings.voice_on_warning && ["ALTA", "MÉDIA"].includes(diagnosis.severity_level);
}

function DiagnosisCard({ diagnosis, settings, onSettingsChange }) {
  const { speaking, speak, stop } = useSpeech(settings);
  const autoVoice = settings.auto_voice;
  const spokenId = React.useRef(null);
  const ready = diagnosis && diagnosis.status === "ready";

  React.useEffect(() => {
    if (!ready || spokenId.current === diagnosis.id) return;
    spokenId.current = diagnosis.id;
    if (shouldNarrate(diagnosis, settings)) speak(diagnosis.speech_summary);
  }, [ready, diagnosis, settings, speak]);

  React.useEffect(() => {
    if (!diagnosis) stop();
  }, [diagnosis, stop]);

  if (!diagnosis) return null;

  const toggleAutoVoice = () => {
    onSettingsChange({ auto_voice: !autoVoice });
    if (autoVoice) stop();
  };
  const tone = SEVERITY_TONE[diagnosis.severity_level] || "info";
  const cause = ready ? causeParts(diagnosis) : null;
  const measures = ready ? measurementItems(diagnosis.measurement) : [];
  const lowConfidence = ready && diagnosis.low_confidence;
  const autotrain = ready && diagnosis.autotrain;
  const learned = ready && diagnosis.confidence_breakdown && diagnosis.confidence_breakdown.validated > 0;

  return (
    <section className={`card diagnosis-card tone-${tone}`} aria-live="polite">
      <header className="diagnosis-head">
        <div className="diagnosis-brand">
          <span className="diagnosis-icon">
            <SparkIcon />
          </span>
          <div>
            <h2 className="diagnosis-title">Diagnóstico IA</h2>
            <span className="diagnosis-sub">
              {learned ? "Inclui ocorrência validada pela engenharia" : "Análise com base técnica normativa"}
            </span>
          </div>
        </div>
        <div className="diagnosis-actions">
          {diagnosis.severity_level && (
            <span className={`severity-pill tone-${tone}`}>
              <i />
              {SEVERITY_TEXT[diagnosis.severity_level] || diagnosis.severity_level}
            </span>
          )}
          {SPEECH_SUPPORTED && ready && (
            <VoiceButton speaking={speaking} onClick={() => (speaking ? stop() : speak(diagnosis.speech_summary))} />
          )}
        </div>
      </header>

      {!ready ? (
        <div className="diagnosis-pending" role="status">
          <i />
          <i />
          <span>Consultando a base técnica…</span>
        </div>
      ) : (
        <>
        {lowConfidence && (
          <div className="confidence-alert fade-in" role="note">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M10 3l7.5 13h-15L10 3z" strokeLinejoin="round" />
              <path d="M10 8.5v3.5M10 14.5v.01" />
            </svg>
            <div className="confidence-alert-text">
              <strong>Confiança de {formatPercent(diagnosis.confidence_score)} · abaixo do limiar de {diagnosis.confidence_threshold}%</strong>
              <span>
                Tratado como hipótese.{" "}
                {autotrain
                  ? `A ocorrência entrou na curadoria (lacuna #${autotrain.gap_id}) e a base será retreinada após a validação.`
                  : "Valide com a engenharia antes de agir."}
              </span>
            </div>
            {autotrain && <span className="autotrain-chip">Autotreinamento ativado</span>}
          </div>
        )}
        <div className="diagnosis-content fade-in" key={diagnosis.id}>
          <div className="diagnosis-cause">
            {lowConfidence && <span className="hypothesis-label">Hipótese</span>}
            <p className="cause-headline">{cause.headline}</p>
            {cause.detail && <p className="cause-detail">{cause.detail}</p>}
            {measures.length > 0 && (
              <ul className="measure-list" aria-label="Medição">
                {measures.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            )}
          </div>

          <div className="action-groups">
            {ACTION_GROUPS.map((group) => {
              const items = diagnosis.recommended_solutions.filter((s) => s.type === group.type);
              if (!items.length) return null;
              return (
                <div key={group.type} className={`action-group type-${group.type.toLowerCase()}`}>
                  <span className="action-title">{group.title}</span>
                  <ol>
                    {items.map((s, i) => (
                      <li key={s.step} className={s.contingency ? "is-contingency" : ""}>
                        <b>{i + 1}</b>
                        <span>{s.action}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              );
            })}
          </div>
        </div>
        </>
      )}

      {ready && (
        <footer className="diagnosis-foot">
          <div className="ref-chips">
            {groupedRefs(diagnosis.norm_references).map((g) => (
              <span key={g.source} className={`ref-chip${g.validated ? " is-validated" : ""}`} title={g.detail}>
                {g.validated && (
                  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M3.5 8.5l3 3 6-7" />
                  </svg>
                )}
                {g.source}
              </span>
            ))}
          </div>
          <div className="diagnosis-meta">
            <span className={`confidence${lowConfidence ? " is-low" : ""}`} title={confidenceTooltip(diagnosis)}>
              Confiança
              <span className="confidence-track">
                <i style={{ width: `${diagnosis.confidence_score}%` }} />
                <em style={{ left: `${diagnosis.confidence_threshold || 90}%` }} aria-hidden="true" />
              </span>
              <b>{formatPercent(diagnosis.confidence_score)}</b>
            </span>
            {SPEECH_SUPPORTED && (
              <label className="voice-switch">
                <input type="checkbox" role="switch" checked={autoVoice} onChange={toggleAutoVoice} />
                <span className="switch-track" aria-hidden="true" />
                Voz automática
              </label>
            )}
          </div>
        </footer>
      )}
    </section>
  );
}
