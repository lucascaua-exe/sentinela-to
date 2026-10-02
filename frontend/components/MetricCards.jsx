const PHASE_STATUS = {
  idle: { level: "idle", text: "Sem leitura" },
  normal: { level: "normal", text: "Normal" },
  sag: { level: "critical", text: "Subtensão" },
  swell: { level: "critical", text: "Sobretensão" },
  low: { level: "warning", text: "Tensão baixa" },
  high: { level: "warning", text: "Tensão alta" },
  lost: { level: "critical", text: "Sem tensão" },
};

function phaseStatus(v) {
  const { interruption, precarious, adequate } = VOLTAGE_LIMITS;
  if (v == null) return "idle";
  if (v < interruption) return "lost";
  if (v < precarious[0]) return "sag";
  if (v > precarious[1]) return "swell";
  if (v < adequate[0]) return "low";
  if (v > adequate[1]) return "high";
  return "normal";
}

function AnimatedNumber({ value, decimals = 1 }) {
  const ref = React.useRef(null);
  const shown = React.useRef(null);

  React.useEffect(() => {
    const el = ref.current;
    if (value == null) {
      el.textContent = "—";
      shown.current = null;
      return;
    }
    if (document.hidden || shown.current == null) {
      el.textContent = value.toFixed(decimals);
      shown.current = value;
      return;
    }
    const tween = { v: shown.current };
    const animation = anime({
      targets: tween,
      v: value,
      duration: 450,
      easing: "easeOutQuad",
      update: () => {
        el.textContent = tween.v.toFixed(decimals);
      },
    });
    return () => {
      animation.pause();
      shown.current = tween.v;
    };
  }, [value]);

  return <span ref={ref} className="metric-number" />;
}

function MetricCard({ label, color, value }) {
  const status = phaseStatus(value);
  const info = PHASE_STATUS[status];
  const delta = value == null ? null : ((value - NOMINAL_V) / NOMINAL_V) * 100;
  return (
    <div className={`card metric-card is-${info.level}`} style={{ "--phase": color }}>
      <div className="metric-head">
        <span className="metric-label">
          <i className="phase-swatch" />
          {label}
        </span>
        {status !== "normal" && <span className="metric-status fade-in">{info.text}</span>}
      </div>
      <div className="metric-value">
        <AnimatedNumber value={value} />
        <span className="metric-unit">V</span>
      </div>
      <div className="metric-foot">
        {delta == null ? "\u00a0" : `${delta >= 0 ? "+" : "−"}${Math.abs(delta).toFixed(1)}% do nominal`}
      </div>
    </div>
  );
}

function MetricCards({ sample }) {
  return (
    <section className="metrics">
      {PHASES.map((p) => (
        <MetricCard key={p.key} label={p.label} color={p.color} value={sample ? sample[p.key] : null} />
      ))}
    </section>
  );
}

const SOURCE_LABEL = { GRID: "Rede", ARMED: "Rede · armada", BACKUP: "Solar + BESS", RESTORING: "Sincronizando" };

function GridStats({ sample, ats }) {
  const fmt = (v, d) => (v == null ? "—" : v.toFixed(d));
  const items = [
    { label: "Corrente", value: fmt(sample && sample.current_total, 1), unit: "A" },
    { label: "Frequência", value: fmt(sample && sample.frequency, 2), unit: "Hz" },
    { label: "Fator de potência", value: fmt(sample && sample.power_factor, 2), unit: "" },
    { label: "Fonte", value: ats ? SOURCE_LABEL[ats.state] : "—", unit: "" },
  ];
  return (
    <section className="card grid-stats">
      {items.map((it) => (
        <div key={it.label} className="stat">
          <span className="label">{it.label}</span>
          <span className="stat-value">
            {it.value}
            {it.unit && <small>{it.unit}</small>}
          </span>
        </div>
      ))}
    </section>
  );
}
