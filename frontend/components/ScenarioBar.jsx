const SCENARIOS = [
  { mode: "NORMAL", title: "Operação normal", hint: "220 V · 60 Hz" },
  { mode: "SAG_PALMAS", title: "Afundamento", hint: "168 V · 58,1 Hz" },
  { mode: "SWELL", title: "Sobretensão", hint: "Pico 248,5 V" },
  { mode: "PHASE_LOSS", title: "Queda de fase", hint: "Fase C · 0 V" },
  { mode: "PRE_FAULT", title: "Pré-falha", hint: "Degradação lenta · só a IA percebe no início" },
];

function ScenarioBar({ commandMode, onSelect }) {
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.target.closest("input, textarea, select, [contenteditable]") || document.querySelector(".site-menu")) return;
      const index = Number(e.key) - 1;
      if (index >= 0 && index < SCENARIOS.length) onSelect(SCENARIOS[index].mode);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onSelect]);

  return (
    <nav className="scenario-bar" aria-label="Simulação de cenários">
      <span className="section-label">Simular</span>
      <div className="segmented" role="group">
        {SCENARIOS.map((s, i) => {
          const active = commandMode === s.mode;
          return (
            <button
              key={s.mode}
              className={`segment ${active ? "is-active" : ""}`}
              aria-pressed={active}
              title={`${s.hint} · tecla ${i + 1}`}
              onClick={() => onSelect(s.mode)}
            >
              {s.title}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
