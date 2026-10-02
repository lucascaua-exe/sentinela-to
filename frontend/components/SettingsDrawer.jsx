const DEFAULT_SETTINGS = {
  auto_voice: false,
  voice_on_warning: false,
  voice_name: "",
  voice_rate: 1.0,
  voice_volume: 1.0,
  alert_sound: false,
  browser_notifications: false,
  default_site: "",
  show_simulator: true,
  report_company: "",
  report_responsible: "",
};
const SETTINGS_SAVE_DELAY_MS = 450;

function useSettings() {
  const [settings, setSettings] = React.useState(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = React.useState(false);
  const [status, setStatus] = React.useState("idle");
  const pending = React.useRef({});
  const timer = React.useRef(null);

  React.useEffect(() => {
    fetch("/api/v1/settings")
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((body) => setSettings({ ...DEFAULT_SETTINGS, ...body }), () => {})
      .finally(() => setLoaded(true));
  }, []);

  const flush = React.useCallback(async () => {
    const changes = pending.current;
    pending.current = {};
    setStatus("saving");
    const resp = await fetch("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }).catch(() => null);
    if (resp && resp.status === 401) location.replace("/login");
    setStatus(resp && resp.ok ? "saved" : "error");
  }, []);

  // Atualização otimista; alterações em sequência (ex.: arrastar o controle de velocidade) viram um único envio.
  const update = React.useCallback(
    (changes) => {
      setSettings((current) => ({ ...current, ...changes }));
      pending.current = { ...pending.current, ...changes };
      clearTimeout(timer.current);
      timer.current = setTimeout(flush, SETTINGS_SAVE_DELAY_MS);
    },
    [flush]
  );

  return { settings, loaded, status, update };
}

function Toggle({ checked, onChange, label, hint, disabled }) {
  return (
    <label className={`setting-row${disabled ? " is-disabled" : ""}`}>
      <span className="setting-text">
        <strong>{label}</strong>
        {hint && <small>{hint}</small>}
      </span>
      <span className="toggle">
        <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span className="toggle-track" aria-hidden="true" />
      </span>
    </label>
  );
}

function SettingsSection({ icon, title, children }) {
  return (
    <section className="settings-section">
      <h3>
        <span className="settings-icon" aria-hidden="true">{icon}</span>
        {title}
      </h3>
      <div className="settings-body">{children}</div>
    </section>
  );
}

const SETTINGS_ICONS = {
  voice: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 8v4h3l4 3V5L6 8H3z" />
      <path d="M13.5 7.5a3.5 3.5 0 0 1 0 5M15.5 5a7 7 0 0 1 0 10" />
    </svg>
  ),
  bell: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 8a5 5 0 0 1 10 0c0 4 1.5 5.5 1.5 5.5h-13S5 12 5 8z" />
      <path d="M8.5 16.5a1.6 1.6 0 0 0 3 0" />
    </svg>
  ),
  panel: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="14" height="12" rx="2" />
      <path d="M3 8h14M8 8v8" />
    </svg>
  ),
  report: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 3h6l3 3v11H6z" />
      <path d="M12 3v3h3M8.5 10h4M8.5 13h4" />
    </svg>
  ),
};

const SAVE_STATUS_TEXT = { saving: "Salvando…", saved: "Alterações salvas", error: "Não foi possível salvar" };

function SettingsDrawer({ open, onClose, settings, status, onChange, sites }) {
  const voices = useVoices();
  const { speaking, speak, stop } = useSpeech(settings);
  const [notifyHint, setNotifyHint] = React.useState("");

  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const toggleNotifications = async (enabled) => {
    if (!enabled) return onChange({ browser_notifications: false });
    if (!NOTIFICATIONS_SUPPORTED) return setNotifyHint("Este navegador não oferece notificações.");
    const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    if (permission !== "granted") {
      setNotifyHint("Permissão negada no navegador. Libere as notificações do site para ativar.");
      return onChange({ browser_notifications: false });
    }
    setNotifyHint("");
    onChange({ browser_notifications: true });
  };

  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer settings-drawer" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header className="drawer-head">
          <div>
            <h2 id="settings-title">Configurações</h2>
            <p>Preferências salvas na sua conta e aplicadas em qualquer computador.</p>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Fechar">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" />
            </svg>
          </button>
        </header>

        <SettingsSection icon={SETTINGS_ICONS.voice} title="Alerta por voz">
          <Toggle
            label="Narração automática"
            hint="Lê o diagnóstico da IA assim que surge um alerta crítico."
            checked={settings.auto_voice}
            disabled={!SPEECH_SUPPORTED}
            onChange={(v) => {
              onChange({ auto_voice: v });
              if (!v) stop();
            }}
          />
          <Toggle
            label="Narrar também alertas de atenção"
            hint="Inclui ocorrências de severidade alta e moderada."
            checked={settings.voice_on_warning}
            disabled={!SPEECH_SUPPORTED || !settings.auto_voice}
            onChange={(v) => onChange({ voice_on_warning: v })}
          />
          <label className="setting-field">
            <span>Voz</span>
            <select value={settings.voice_name} onChange={(e) => onChange({ voice_name: e.target.value })} disabled={!SPEECH_SUPPORTED}>
              <option value="">Automática (melhor voz em português)</option>
              {voices.map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name.replace(/^Microsoft\s+/, "").replace(/\s*-\s*Portuguese.*$/, "")} · {v.lang}
                </option>
              ))}
            </select>
          </label>
          <div className="setting-sliders">
            <label className="setting-field">
              <span>
                Velocidade <b>{settings.voice_rate.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 })}×</b>
              </span>
              <input type="range" min="0.7" max="1.5" step="0.05" value={settings.voice_rate} onChange={(e) => onChange({ voice_rate: Number(e.target.value) })} />
            </label>
            <label className="setting-field">
              <span>
                Volume <b>{Math.round(settings.voice_volume * 100)}%</b>
              </span>
              <input type="range" min="0" max="1" step="0.05" value={settings.voice_volume} onChange={(e) => onChange({ voice_volume: Number(e.target.value) })} />
            </label>
          </div>
          <button className="btn-test-voice" onClick={() => (speaking ? stop() : speak(VOICE_TEST_TEXT))} disabled={!SPEECH_SUPPORTED}>
            {speaking ? "Parar teste" : "Testar voz"}
          </button>
        </SettingsSection>

        <SettingsSection icon={SETTINGS_ICONS.bell} title="Avisos de alerta crítico">
          <Toggle
            label="Som de alerta"
            hint="Toque curto quando a IA detecta uma falha crítica."
            checked={settings.alert_sound}
            onChange={(v) => {
              onChange({ alert_sound: v });
              if (v) playAlertTone(settings.voice_volume);
            }}
          />
          <Toggle
            label="Notificação do navegador"
            hint="Avisa mesmo com o painel em outra aba ou minimizado."
            checked={settings.browser_notifications}
            onChange={toggleNotifications}
          />
          {notifyHint && <p className="setting-hint">{notifyHint}</p>}
        </SettingsSection>

        <SettingsSection icon={SETTINGS_ICONS.panel} title="Painel">
          <label className="setting-field">
            <span>Unidade ao abrir o painel</span>
            <select value={settings.default_site} onChange={(e) => onChange({ default_site: e.target.value })}>
              <option value="">Última unidade visualizada</option>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.city} · {s.unit}
                </option>
              ))}
            </select>
          </label>
          <Toggle
            label="Barra de simulação de cenários"
            hint="Desative para uma apresentação com aparência de operação real."
            checked={settings.show_simulator}
            onChange={(v) => onChange({ show_simulator: v })}
          />
        </SettingsSection>

        <SettingsSection icon={SETTINGS_ICONS.report} title="Relatórios">
          <label className="setting-field">
            <span>Empresa</span>
            <input
              value={settings.report_company}
              maxLength={120}
              placeholder="Ex.: Indústria Exemplo do Tocantins Ltda."
              onChange={(e) => onChange({ report_company: e.target.value })}
            />
          </label>
          <label className="setting-field">
            <span>Responsável técnico</span>
            <input
              value={settings.report_responsible}
              maxLength={120}
              placeholder="Ex.: Eng. Maria Souza · CREA-TO 000000"
              onChange={(e) => onChange({ report_responsible: e.target.value })}
            />
          </label>
          <p className="setting-hint">Aparecem no cabeçalho dos relatórios em PDF.</p>
        </SettingsSection>

        <footer className={`settings-status status-${status}`} role="status">
          {SAVE_STATUS_TEXT[status] || "As alterações são salvas automaticamente."}
        </footer>
      </aside>
    </div>
  );
}
