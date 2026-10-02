const WS_BASE = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`;
const HISTORY_POINTS = 120;
const SITE_STORAGE_KEY = "sentinela.site";
const SITES_POLL_MS = 2000;

const initialFeed = { connected: false, history: [], ats: null, commandMode: "NORMAL", diagnosis: null };

function feedReducer(state, { type, msg, value }) {
  switch (type) {
    case "reset":
      return initialFeed;
    case "connected":
      return { ...state, connected: value };
    case "snapshot":
      return {
        ...state,
        history: msg.history.slice(-HISTORY_POINTS),
        ats: msg.ats,
        commandMode: msg.command_mode,
        diagnosis: msg.diagnosis || null,
      };
    case "diagnosis":
      return { ...state, diagnosis: msg.diagnosis };
    case "telemetry":
      return { ...state, history: [...state.history, msg.record].slice(-HISTORY_POINTS) };
    case "ats":
      return { ...state, ats: msg.ats };
    case "mode":
      return { ...state, commandMode: msg.command_mode };
    default:
      return state;
  }
}

function useLiveFeed(siteId, onAlert) {
  const [state, dispatch] = React.useReducer(feedReducer, initialFeed);
  const onAlertRef = React.useRef(onAlert);
  onAlertRef.current = onAlert;
  React.useEffect(() => {
    let ws;
    let retry;
    let closed = false;
    dispatch({ type: "reset" });
    const connect = () => {
      ws = new WebSocket(`${WS_BASE}?site=${encodeURIComponent(siteId)}`);
      ws.onopen = () => dispatch({ type: "connected", value: true });
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "alert") onAlertRef.current(msg.alert);
        dispatch({ type: msg.type, msg });
      };
      ws.onclose = () => {
        if (closed) return;
        dispatch({ type: "connected", value: false });
        fetch("/api/auth/me").then((r) => {
          if (r.status === 401) location.replace("/login");
          else retry = setTimeout(connect, 1500);
        }, () => (retry = setTimeout(connect, 1500)));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws && ws.close();
    };
  }, [siteId]);
  return state;
}

function useSites() {
  const [sites, setSites] = React.useState([]);
  React.useEffect(() => {
    let timer;
    const load = () =>
      fetch("/api/sites")
        .then((r) => (r.ok ? r.json() : Promise.reject(r)))
        .then(setSites, () => {})
        .finally(() => (timer = setTimeout(load, SITES_POLL_MS)));
    load();
    return () => clearTimeout(timer);
  }, []);
  return sites;
}

function useSelectedSite(sites, settings, settingsLoaded) {
  const urlSite = React.useRef(new URLSearchParams(location.search).get("site"));
  const [siteId, setSiteId] = React.useState(() => urlSite.current || localStorage.getItem(SITE_STORAGE_KEY) || "palmas");
  React.useEffect(() => {
    if (sites.length && !sites.some((s) => s.id === siteId)) setSiteId(sites[0].id);
  }, [sites, siteId]);
  const select = React.useCallback((id) => {
    setSiteId(id);
    localStorage.setItem(SITE_STORAGE_KEY, id);
    window.history.replaceState(null, "", `?site=${encodeURIComponent(id)}${location.hash}`);
  }, []);
  // A unidade padrão vale ao abrir o painel; um link com ?site= tem prioridade.
  const appliedDefault = React.useRef(false);
  React.useEffect(() => {
    if (!settingsLoaded || appliedDefault.current) return;
    appliedDefault.current = true;
    if (!urlSite.current && settings.default_site) setSiteId(settings.default_site);
  }, [settingsLoaded, settings.default_site]);
  return [siteId, select];
}

function currentView() {
  return location.hash.startsWith("#/historico") ? "history" : "panel";
}

function useHashView() {
  const [view, setView] = React.useState(currentView);
  React.useEffect(() => {
    const onHash = () => {
      setView(currentView());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return view;
}

function useCriticalAlertNotifier(settings, siteName) {
  return React.useCallback(
    (alert) => {
      if (alert.level !== "CRITICAL" || alert.source !== "IA") return;
      if (settings.alert_sound) playAlertTone(settings.voice_volume);
      if (settings.browser_notifications && !document.hasFocus()) {
        showAlertNotification(`Alerta crítico · ${siteName}`, alert.title);
      }
    },
    [settings.alert_sound, settings.browser_notifications, settings.voice_volume, siteName]
  );
}

async function postJson(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401) location.replace("/login");
  return resp;
}

function useCurrentUser() {
  const [user, setUser] = React.useState(null);
  React.useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((body) => setUser(body.username), () => location.replace("/login"));
  }, []);
  return user;
}

function logout() {
  fetch("/api/auth/logout", { method: "POST" }).finally(() => location.replace("/login"));
}

function App() {
  const sites = useSites();
  const view = useHashView();
  const { settings, loaded: settingsLoaded, status: settingsStatus, update: updateSettings } = useSettings();
  const [siteId, selectSite] = useSelectedSite(sites, settings, settingsLoaded);
  const currentSite = sites.find((s) => s.id === siteId);
  const notifyAlert = useCriticalAlertNotifier(settings, currentSite ? currentSite.city : siteId);
  const { connected, history, ats, commandMode, diagnosis } = useLiveFeed(siteId, notifyAlert);
  const user = useCurrentUser();
  const [knowledge, reloadKnowledge] = useKnowledgeGaps();
  const [curationOpen, setCurationOpen] = React.useState(false);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const closeCuration = React.useCallback(() => setCurationOpen(false), []);
  const closeSettings = React.useCallback(() => setSettingsOpen(false), []);
  // O feed só recebe mensagens enquanto chega telemetria; a presença do medidor vem de /api/sites.
  const live = Boolean(connected && currentSite && currentSite.online);
  const sample = live && history.length ? history[history.length - 1] : null;
  const critical = Boolean(sample && sample.analysis.severity === "CRITICAL");
  const selectScenario = React.useCallback(
    (mode) => postJson("/api/simulator/mode", { mode, site: siteId }),
    [siteId]
  );
  const commandTransfer = React.useCallback(
    (action) => postJson(`/api/ats/${action}?site=${encodeURIComponent(siteId)}`),
    [siteId]
  );

  return (
    <div className="app">
      <Header
        live={live}
        critical={critical}
        user={user}
        onLogout={logout}
        sites={sites}
        siteId={siteId}
        onSiteChange={selectSite}
        openGaps={knowledge.gaps.length}
        onOpenCuration={() => setCurationOpen(true)}
        view={view}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      {view === "history" ? (
        <HistoryView sites={sites} />
      ) : (
        <>
          {settings.show_simulator && <ScenarioBar commandMode={commandMode} onSelect={selectScenario} />}
          <TransferBar ats={ats} sample={sample} onCommand={commandTransfer} />
          <DiagnosisCard diagnosis={live ? diagnosis : null} settings={settings} onSettingsChange={updateSettings} />
          <MetricCards sample={sample} />
          <GridStats sample={sample} ats={ats} />
          <RealtimeChart history={history} />
        </>
      )}
      <CurationDrawer open={curationOpen} data={knowledge} onClose={closeCuration} onChanged={reloadKnowledge} />
      <SettingsDrawer
        open={settingsOpen}
        onClose={closeSettings}
        settings={settings}
        status={settingsStatus}
        onChange={updateSettings}
        sites={sites}
      />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
