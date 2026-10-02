const HISTORY_REFRESH_MS = 10000;
const HISTORY_PAGE_SIZE = 50;
const DAY_S = 86400;

const PERIOD_OPTIONS = [
  { id: "today", label: "Hoje", days: 1 },
  { id: "7d", label: "7 dias", days: 7 },
  { id: "30d", label: "30 dias", days: 30 },
  { id: "all", label: "Tudo" },
  { id: "custom", label: "Personalizado" },
];

const TRANSFER_OPTIONS = [
  { id: "", label: "Todas" },
  { id: "true", label: "Com comutação" },
  { id: "false", label: "Sem comutação" },
];

const EVENT_SOURCE_TEXT = { IA: "Detecção", ATS: "Chave de transferência" };

const INITIAL_HISTORY_FILTERS = {
  period: "7d",
  from: "",
  to: "",
  sites: [],
  severities: [],
  codes: [],
  transferred: "",
  q: "",
};

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function toDateInput(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function periodRange(filters) {
  const option = PERIOD_OPTIONS.find((p) => p.id === filters.period);
  if (option && option.days) {
    const today = startOfDay(new Date());
    return { start: today.getTime() / 1000 - (option.days - 1) * DAY_S, end: null };
  }
  if (filters.period === "custom") {
    const start = filters.from ? new Date(`${filters.from}T00:00:00`).getTime() / 1000 : null;
    const end = filters.to ? new Date(`${filters.to}T23:59:59`).getTime() / 1000 : null;
    return { start, end };
  }
  return { start: null, end: null };
}

function historyQuery(filters) {
  const { start, end } = periodRange(filters);
  const params = new URLSearchParams();
  if (start != null) params.set("start", String(Math.floor(start)));
  if (end != null) params.set("end", String(Math.floor(end)));
  if (filters.sites.length) params.set("sites", filters.sites.join(","));
  if (filters.severities.length) params.set("severities", filters.severities.join(","));
  if (filters.codes.length) params.set("codes", filters.codes.join(","));
  if (filters.transferred) params.set("transferred", filters.transferred);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  return params.toString();
}

function activeFilterCount(filters) {
  return (
    filters.sites.length +
    filters.severities.length +
    filters.codes.length +
    (filters.transferred ? 1 : 0) +
    (filters.q.trim() ? 1 : 0) +
    (filters.period !== INITIAL_HISTORY_FILTERS.period ? 1 : 0)
  );
}

function toggleValue(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function useHistory(query, limit) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  React.useEffect(() => {
    let timer;
    let cancelled = false;
    setLoading(true);
    const load = () =>
      fetch(`/api/v1/history/episodes?${query}${query ? "&" : ""}limit=${limit}`)
        .then((r) => {
          if (r.status === 401) location.replace("/login");
          return r.ok ? r.json() : Promise.reject(r);
        })
        .then((body) => !cancelled && setData(body), () => {})
        .finally(() => {
          if (cancelled) return;
          setLoading(false);
          timer = setTimeout(load, HISTORY_REFRESH_MS);
        });
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, limit]);
  return { data, loading };
}

function dailySeries(byDay, filters) {
  const { start, end } = periodRange(filters);
  const keys = Object.keys(byDay);
  if (!keys.length && start == null) return [];
  const first = start != null ? startOfDay(new Date(start * 1000)) : new Date(`${keys[0]}T00:00:00`);
  const last = end != null ? new Date(end * 1000) : new Date();
  const days = [];
  for (let d = first; d <= last && days.length < 120; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)) {
    const key = toDateInput(d);
    days.push({ key, label: d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }), ...(byDay[key] || {}) });
  }
  return days;
}

function HistoryChart({ stats, filters }) {
  const hostRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    const chart = echarts.init(hostRef.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(hostRef.current);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, []);

  React.useEffect(() => {
    const days = dailySeries(stats ? stats.by_day : {}, filters);
    const bar = (name, key, color) => ({
      name,
      type: "bar",
      stack: "total",
      barMaxWidth: 26,
      data: days.map((d) => d[key] || 0),
      itemStyle: { color, borderRadius: key === "CRITICAL" ? [4, 4, 0, 0] : 0 },
      emphasis: { focus: "series" },
    });
    chartRef.current.setOption(
      {
        animationDuration: 400,
        textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
        grid: { left: 4, right: 8, top: 28, bottom: 0, containLabel: true },
        legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: "#64748B", fontSize: 12 } },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(148,163,184,0.12)" } },
          backgroundColor: "#FFFFFF",
          borderColor: "#E2E8F0",
          textStyle: { color: "#0F172A", fontSize: 12 },
          extraCssText: "border-radius:8px;box-shadow:0 4px 16px rgba(15,23,42,0.08);",
        },
        xAxis: {
          type: "category",
          data: days.map((d) => d.label),
          axisLine: { lineStyle: { color: "#E2E8F0" } },
          axisTick: { show: false },
          axisLabel: { color: "#64748B", fontSize: 11, hideOverlap: true },
        },
        yAxis: {
          type: "value",
          minInterval: 1,
          axisLabel: { color: "#64748B", fontSize: 11 },
          splitLine: { lineStyle: { color: "#F1F5F9" } },
        },
        series: [bar("Atenção", "WARNING", "#F59E0B"), bar("Crítica", "CRITICAL", "#DC2626")],
      },
      { notMerge: true }
    );
  }, [stats, filters]);

  return <div className="history-chart" ref={hostRef} />;
}

function FilterChip({ active, onClick, children, tone }) {
  return (
    <button type="button" className={`filter-chip${active ? " is-active" : ""}${tone ? ` tone-${tone}` : ""}`} aria-pressed={active} onClick={onClick}>
      {children}
    </button>
  );
}

function HistoryFilters({ filters, onChange, sites, onReset }) {
  const [search, setSearch] = React.useState(filters.q);
  React.useEffect(() => setSearch(filters.q), [filters.q]);
  React.useEffect(() => {
    if (search === filters.q) return undefined;
    const t = setTimeout(() => onChange({ q: search }), 350);
    return () => clearTimeout(t);
  }, [search]);

  const count = activeFilterCount(filters);

  return (
    <section className="card history-filters">
      <div className="filter-row">
        <span className="filter-label">Período</span>
        <div className="filter-group">
          {PERIOD_OPTIONS.map((p) => (
            <FilterChip
              key={p.id}
              active={filters.period === p.id}
              onClick={() => {
                const changes = { period: p.id };
                if (p.id === "custom" && !filters.from) {
                  changes.from = toDateInput(new Date(Date.now() - 6 * DAY_S * 1000));
                  changes.to = toDateInput(new Date());
                }
                onChange(changes);
              }}
            >
              {p.label}
            </FilterChip>
          ))}
          {filters.period === "custom" && (
            <span className="date-range">
              <input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => onChange({ from: e.target.value })} aria-label="Data inicial" />
              <span>até</span>
              <input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => onChange({ to: e.target.value })} aria-label="Data final" />
            </span>
          )}
        </div>
      </div>

      <div className="filter-row">
        <span className="filter-label">Unidades</span>
        <div className="filter-group">
          <FilterChip active={!filters.sites.length} onClick={() => onChange({ sites: [] })}>
            Todas
          </FilterChip>
          {sites.map((s) => (
            <FilterChip key={s.id} active={filters.sites.includes(s.id)} onClick={() => onChange({ sites: toggleValue(filters.sites, s.id) })}>
              {s.city}
            </FilterChip>
          ))}
        </div>
      </div>

      <div className="filter-row">
        <span className="filter-label">Severidade</span>
        <div className="filter-group">
          {["CRITICAL", "WARNING"].map((sev) => (
            <FilterChip
              key={sev}
              tone={sev.toLowerCase()}
              active={filters.severities.includes(sev)}
              onClick={() => onChange({ severities: toggleValue(filters.severities, sev) })}
            >
              <i className="sev-dot" />
              {SEVERITY_LABELS[sev]}
            </FilterChip>
          ))}
          <span className="filter-divider" />
          {TRANSFER_OPTIONS.map((t) => (
            <FilterChip key={t.id || "all"} active={filters.transferred === t.id} onClick={() => onChange({ transferred: t.id })}>
              {t.label}
            </FilterChip>
          ))}
        </div>
      </div>

      <div className="filter-row">
        <span className="filter-label">Distúrbio</span>
        <div className="filter-group">
          {Object.entries(CODE_LABELS).map(([code, label]) => (
            <FilterChip key={code} active={filters.codes.includes(code)} onClick={() => onChange({ codes: toggleValue(filters.codes, code) })}>
              {label}
            </FilterChip>
          ))}
        </div>
      </div>

      <div className="filter-row filter-row-last">
        <label className="search-field">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <circle cx="9" cy="9" r="5.5" />
            <path d="M13.2 13.2L17 17" />
          </svg>
          <input value={search} maxLength={80} placeholder="Buscar por descrição ou causa provável" onChange={(e) => setSearch(e.target.value)} />
        </label>
        {count > 0 && (
          <button className="btn-link" onClick={onReset}>
            Limpar filtros ({count})
          </button>
        )}
      </div>
    </section>
  );
}

function HistoryKpis({ stats }) {
  const items = [
    { label: "Ocorrências", value: stats.total },
    { label: "Críticas", value: stats.critical, tone: "critical" },
    { label: "Comutações para backup", value: stats.transfers, tone: "accent" },
    { label: "Tempo total em anomalia", value: formatDuration(stats.total_duration_s) },
  ];
  return (
    <div className="history-kpis">
      {items.map((k) => (
        <div key={k.label} className={`history-kpi${k.tone ? ` tone-${k.tone}` : ""}`}>
          <span>{k.label}</span>
          <b>{k.value}</b>
        </div>
      ))}
    </div>
  );
}

function formatMeasure(value, unit, digits = 1) {
  return value == null ? "—" : `${Number(value).toLocaleString("pt-BR", { maximumFractionDigits: digits })}${unit}`;
}

function EpisodeDetail({ episodeId, fallback }) {
  const [detail, setDetail] = React.useState(null);
  const [failed, setFailed] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    fetch(`/api/v1/history/episodes/${episodeId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((body) => !cancelled && setDetail(body), () => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [episodeId, fallback.ongoing ? fallback.duration_s : fallback.ended_at]);

  const ep = detail || fallback;
  const extremes = [
    { label: "Tensão mínima", value: formatMeasure(ep.min_voltage, " V") },
    { label: "Tensão máxima", value: formatMeasure(ep.max_voltage, " V") },
    { label: "Corrente máxima", value: formatMeasure(ep.max_current, " A") },
    { label: "Fator de potência mín.", value: formatMeasure(ep.min_power_factor, "", 2) },
    { label: "THD máxima", value: formatMeasure(ep.max_thd, "%") },
  ];

  return (
    <div className="episode-detail">
      <div className="episode-extremes">
        {extremes.map((x) => (
          <span key={x.label}>
            <small>{x.label}</small>
            <b>{x.value}</b>
          </span>
        ))}
      </div>

      <div className="episode-columns">
        <div className="episode-analysis">
          {ep.diagnosis_cause ? (
            <>
              <h5>
                {ep.low_confidence ? "Hipótese da IA" : "Causa provável"}
                {ep.diagnosis_confidence != null && (
                  <span className={`conf-pill${ep.low_confidence ? " is-low" : ""}`}>{formatPercent(ep.diagnosis_confidence, 0)} de confiança</span>
                )}
              </h5>
              <p>{ep.diagnosis_cause}</p>
              {ep.measurement && <p className="episode-measure">{ep.measurement}</p>}
            </>
          ) : (
            <p className="episode-muted">Sem diagnóstico da IA registrado para esta ocorrência.</p>
          )}
          {ep.actions && ep.actions.length > 0 && (
            <>
              <h5>Ações recomendadas</h5>
              <ol className="episode-actions">
                {ep.actions.map((a, i) => (
                  <li key={i}>
                    <span className={`action-tag tag-${a.type.toLowerCase()}`}>{a.type === "IMEDIATA" ? "Agora" : "Prevenção"}</span>
                    {a.action}
                  </li>
                ))}
              </ol>
            </>
          )}
          {ep.norm_references && ep.norm_references.length > 0 && (
            <div className="episode-refs">
              {groupedRefs(ep.norm_references).map((g) => (
                <span key={g.source} className={`ref-chip${g.validated ? " is-validated" : ""}`} title={g.detail}>
                  {g.source}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="episode-events">
          <h5>Sequência de eventos</h5>
          {!detail && !failed && <p className="episode-muted">Carregando…</p>}
          {failed && <p className="episode-muted">Não foi possível carregar os eventos.</p>}
          {detail && (
            <ol className="event-list">
              {detail.events.map((ev, i) => (
                <li key={i} className={`event-item level-${ev.level.toLowerCase()}`}>
                  <time>{formatClock(ev.ts)}</time>
                  <div>
                    <strong>{ev.title}</strong>
                    <small>
                      {EVENT_SOURCE_TEXT[ev.source] || ev.source}
                      {ev.detail ? ` · ${ev.detail}` : ""}
                    </small>
                  </div>
                </li>
              ))}
              {!ep.ongoing && (
                <li className="event-item level-ok">
                  <time>{formatClock(ep.ended_at)}</time>
                  <div>
                    <strong>Rede normalizada</strong>
                    <small>Fim da ocorrência após {formatDuration(ep.duration_s)}</small>
                  </div>
                </li>
              )}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

function EpisodeItem({ ep, siteName, expanded, onToggle }) {
  const sev = ep.peak_severity.toLowerCase();
  return (
    <li className={`timeline-item sev-${sev}${expanded ? " is-open" : ""}${ep.ongoing ? " is-ongoing" : ""}`}>
      <span className="timeline-dot" aria-hidden="true" />
      <div className="timeline-card">
        <button className="timeline-summary" onClick={onToggle} aria-expanded={expanded}>
          <span className="timeline-time">
            <b>{formatClock(ep.started_at)}</b>
            <small>{ep.ongoing ? "em curso" : formatDuration(ep.duration_s)}</small>
          </span>
          <span className="timeline-main">
            <span className="timeline-title">
              <span className={`sev-badge sev-${sev}`}>{SEVERITY_LABELS[ep.peak_severity]}</span>
              {ep.title}
            </span>
            <span className="timeline-meta">
              <span>{siteName}</span>
              {ep.codes.map((c) => (
                <span key={c} className="code-chip">
                  {CODE_LABELS[c] || c}
                </span>
              ))}
            </span>
          </span>
          <span className="timeline-flags">
            {ep.ongoing && <span className="flag flag-live">Ao vivo</span>}
            {ep.transferred && <span className="flag flag-backup">Comutou para backup</span>}
            {ep.diagnosis_confidence != null && (
              <span className={`flag${ep.low_confidence ? " flag-low" : ""}`} title="Confiança do diagnóstico da IA">
                IA {formatPercent(ep.diagnosis_confidence, 0)}
              </span>
            )}
            <svg className="chevron" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 8l4 4 4-4" />
            </svg>
          </span>
        </button>
        {expanded && <EpisodeDetail episodeId={ep.id} fallback={ep} />}
      </div>
    </li>
  );
}

function groupByDay(episodes) {
  const groups = [];
  episodes.forEach((ep) => {
    const key = new Date(ep.started_at * 1000).toDateString();
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(ep);
    else groups.push({ key, label: formatDayLabel(ep.started_at), items: [ep] });
  });
  return groups;
}

function ExportButtons({ query, total }) {
  const href = (fmt) => `/api/v1/reports/occurrences.${fmt}${query ? `?${query}` : ""}`;
  const disabled = !total;
  return (
    <div className="export-actions">
      <a className={`btn-export btn-export-primary${disabled ? " is-disabled" : ""}`} href={disabled ? undefined : href("pdf")} download aria-disabled={disabled}>
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M10 3v10M6 9l4 4 4-4M4 16h12" />
        </svg>
        Relatório PDF
      </a>
      <a className={`btn-export${disabled ? " is-disabled" : ""}`} href={disabled ? undefined : href("csv")} download aria-disabled={disabled}>
        Exportar planilha (CSV)
      </a>
    </div>
  );
}

function HistoryView({ sites }) {
  const [filters, setFilters] = React.useState(INITIAL_HISTORY_FILTERS);
  const [limit, setLimit] = React.useState(HISTORY_PAGE_SIZE);
  const [expanded, setExpanded] = React.useState(null);
  const query = React.useMemo(() => historyQuery(filters), [filters]);
  const { data, loading } = useHistory(query, limit);

  const changeFilters = React.useCallback((changes) => {
    setFilters((f) => ({ ...f, ...changes }));
    setLimit(HISTORY_PAGE_SIZE);
  }, []);
  const resetFilters = () => changeFilters(INITIAL_HISTORY_FILTERS);

  const siteNames = React.useMemo(() => Object.fromEntries(sites.map((s) => [s.id, `${s.city} · ${s.unit}`])), [sites]);
  const episodes = data ? data.episodes : [];
  const stats = data ? data.stats : null;
  const groups = groupByDay(episodes);

  return (
    <main className="history">
      <div className="history-head">
        <div>
          <h1>Histórico de ocorrências</h1>
          <p>Analise falhas anteriores, compare unidades e gere relatórios com a identidade do Sentinela TO.</p>
        </div>
        <ExportButtons query={query} total={data ? data.total : 0} />
      </div>

      <HistoryFilters filters={filters} onChange={changeFilters} sites={sites} onReset={resetFilters} />

      {stats && (
        <section className="card history-overview">
          <HistoryKpis stats={stats} />
          <div className="history-chart-wrap">
            <h4 className="card-title">Ocorrências por dia</h4>
            <HistoryChart stats={stats} filters={filters} />
          </div>
        </section>
      )}

      <section className="card history-timeline">
        <div className="card-head">
          <h4 className="card-title">
            Linha do tempo
            {data && <span className="card-title-muted">{data.total === 1 ? "1 ocorrência" : `${data.total} ocorrências`}</span>}
          </h4>
          {loading && <span className="loading-dot" aria-label="Atualizando" />}
        </div>

        {data && !episodes.length && (
          <div className="drawer-empty">
            <strong>Nenhuma ocorrência encontrada</strong>
            {activeFilterCount(filters) ? "Ajuste ou limpe os filtros para ampliar a busca." : "Os eventos de qualidade de energia aparecerão aqui assim que forem detectados."}
          </div>
        )}

        {groups.map((g) => (
          <div key={g.key} className="timeline-day">
            <h5 className="timeline-day-label">
              {g.label}
              <span>{g.items.length}</span>
            </h5>
            <ol className="timeline">
              {g.items.map((ep) => (
                <EpisodeItem
                  key={ep.id}
                  ep={ep}
                  siteName={siteNames[ep.site_id] || ep.site_id}
                  expanded={expanded === ep.id}
                  onToggle={() => setExpanded(expanded === ep.id ? null : ep.id)}
                />
              ))}
            </ol>
          </div>
        ))}

        {data && episodes.length < data.total && (
          <button className="btn-load-more" onClick={() => setLimit((l) => l + HISTORY_PAGE_SIZE)}>
            Carregar mais ({data.total - episodes.length} restantes)
          </button>
        )}
      </section>
    </main>
  );
}
