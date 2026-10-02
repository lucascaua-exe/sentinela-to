const WINDOW_S = 60;
// O traço é desenhado levemente atrasado para que cada novo ponto entre deslizando pela borda direita.
const RENDER_LAG_S = 0.6;
const AXIS_TEXT = "#64748B";

function criticalRanges(history) {
  const ranges = [];
  let start = null;
  history.forEach((h, i) => {
    const critical = h.analysis.severity === "CRITICAL";
    if (critical && start == null) start = h.timestamp;
    const closes = start != null && (!critical || i === history.length - 1);
    if (closes) {
      const ongoing = critical && i === history.length - 1;
      const end = ongoing ? h.timestamp + RENDER_LAG_S + 1 : h.timestamp;
      ranges.push([{ xAxis: start * 1000 }, { xAxis: end * 1000 }]);
      start = null;
    }
  });
  return ranges;
}

function tooltipHtml(params) {
  const rows = params
    .filter((p) => Array.isArray(p.value))
    .map(
      (p) =>
        `<div class="tt-row"><i style="background:${p.color}"></i><span>${p.seriesName}</span><b>${p.value[1].toFixed(1)} V</b></div>`
    )
    .join("");
  const time = params.length ? echarts.time.format(params[0].value[0], "{HH}:{mm}:{ss}", false) : "";
  return `<div class="tt-time">${time}</div>${rows}`;
}

function referenceMarks() {
  return {
    markArea: {
      silent: true,
      itemStyle: { color: "rgba(22, 163, 74, 0.06)" },
      label: { show: false },
      data: [[{ yAxis: VOLTAGE_LIMITS.adequate[0] }, { yAxis: VOLTAGE_LIMITS.adequate[1] }]],
    },
  };
}

function baseOption() {
  return {
    animation: false,
    textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
    grid: { left: 8, right: 16, top: 12, bottom: 4, containLabel: true },
    legend: { show: false, data: PHASES.map((s) => s.label) },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#FFFFFF",
      borderColor: "#E2E8F0",
      borderWidth: 1,
      padding: [8, 12],
      extraCssText: "border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);",
      textStyle: { color: "#0F172A", fontSize: 12 },
      axisPointer: { type: "line", lineStyle: { color: "#CBD5E1", width: 1 } },
      formatter: tooltipHtml,
    },
    xAxis: {
      type: "time",
      splitNumber: 4,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: AXIS_TEXT,
        fontSize: 11,
        hideOverlap: true,
        formatter: (v) => echarts.time.format(v, "{HH}:{mm}:{ss}", false),
      },
    },
    yAxis: {
      type: "value",
      min: 140,
      max: 260,
      interval: 40,
      axisLabel: { color: AXIS_TEXT, fontSize: 11, formatter: "{value} V" },
      splitLine: { lineStyle: { color: "#F1F5F9" } },
    },
    series: [
      ...PHASES.map((s, i) => ({
        name: s.label,
        type: "line",
        data: [],
        smooth: 0.35,
        showSymbol: false,
        sampling: "lttb",
        lineStyle: { width: i === 0 ? 2.5 : 2, color: s.color, cap: "round", join: "round" },
        itemStyle: { color: s.color },
        emphasis: { focus: "series", lineStyle: { width: 3 } },
        z: 3 - i,
        ...(i === 0 ? referenceMarks() : {}),
      })),
      {
        name: "Eventos críticos",
        type: "line",
        data: [],
        silent: true,
        tooltip: { show: false },
        markArea: { silent: true, itemStyle: { color: "rgba(220, 38, 38, 0.07)" }, data: [] },
      },
    ],
  };
}

function RealtimeChart({ history }) {
  const hostRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const clockOffset = React.useRef(null);
  const renderRef = React.useRef(() => {});
  const [hidden, setHidden] = React.useState({});

  React.useEffect(() => {
    const chart = echarts.init(hostRef.current, null, { renderer: "canvas" });
    chart.setOption(baseOption());
    chartRef.current = chart;

    renderRef.current = () => {
      if (clockOffset.current == null) return;
      const now = Date.now() / 1000 - clockOffset.current - RENDER_LAG_S;
      chart.setOption(
        { xAxis: { min: (now - WINDOW_S) * 1000, max: now * 1000 } },
        { lazyUpdate: true, silent: true }
      );
    };

    let raf;
    const frame = () => {
      renderRef.current();
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(hostRef.current);
    return () => {
      cancelAnimationFrame(raf);
      resize.disconnect();
      chart.dispose();
    };
  }, []);

  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (!history.length) {
      clockOffset.current = null;
      chart.setOption({
        series: [...PHASES.map(() => ({ data: [] })), { markArea: { data: [] } }],
      });
      return;
    }

    const last = history[history.length - 1];
    const offset = Date.now() / 1000 - last.timestamp;
    clockOffset.current = clockOffset.current == null ? offset : clockOffset.current * 0.9 + offset * 0.1;

    const lowest = Math.min(...history.map((h) => Math.min(h.voltage_a, h.voltage_b, h.voltage_c)));
    const fullScale = lowest < 140;

    chart.setOption({
      yAxis: { min: fullScale ? 0 : 140, interval: fullScale ? 65 : 40 },
      series: [
        ...PHASES.map((s) => ({ data: history.map((h) => [h.timestamp * 1000, h[s.key]]) })),
        { markArea: { data: criticalRanges(history) } },
      ],
    });
    renderRef.current();
  }, [history]);

  const toggle = (name) => {
    chartRef.current.dispatchAction({ type: "legendToggleSelect", name });
    setHidden((h) => ({ ...h, [name]: !h[name] }));
  };

  return (
    <section className="card chart-card">
      <div className="card-head">
        <h4 className="card-title">
          Tensão da rede <span className="card-title-muted">últimos {WINDOW_S} s</span>
        </h4>
        <div className="chart-legend" role="group" aria-label="Fases exibidas">
          {PHASES.map((s) => (
            <button
              key={s.key}
              className={`legend-item ${hidden[s.label] ? "is-off" : ""}`}
              aria-pressed={!hidden[s.label]}
              title={hidden[s.label] ? `Mostrar ${s.label}` : `Ocultar ${s.label}`}
              onClick={() => toggle(s.label)}
            >
              <i style={{ background: s.color }} />
              {s.label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-body" ref={hostRef} />
    </section>
  );
}
