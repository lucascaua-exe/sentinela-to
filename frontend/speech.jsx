const SPEECH_SUPPORTED = typeof window !== "undefined" && "speechSynthesis" in window;
const NOTIFICATIONS_SUPPORTED = typeof window !== "undefined" && "Notification" in window;
const VOICE_TEST_TEXT = "Alerta crítico na unidade Palmas: teste do alerta por voz do Sentinela TO.";

function portugueseVoices() {
  if (!SPEECH_SUPPORTED) return [];
  return window.speechSynthesis
    .getVoices()
    .filter((v) => v.lang && v.lang.toLowerCase().startsWith("pt"))
    .sort((a, b) => (b.lang === "pt-BR") - (a.lang === "pt-BR"));
}

function pickVoice(name) {
  const voices = portugueseVoices();
  return (
    (name && voices.find((v) => v.name === name)) ||
    voices.find((v) => v.lang === "pt-BR" && /natural|online/i.test(v.name)) ||
    voices.find((v) => v.lang === "pt-BR") ||
    voices[0] ||
    null
  );
}

// Chrome/Edge carregam a lista de vozes de forma assíncrona.
function useVoices() {
  const [voices, setVoices] = React.useState(portugueseVoices);
  React.useEffect(() => {
    if (!SPEECH_SUPPORTED) return undefined;
    const load = () => setVoices(portugueseVoices());
    load();
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", load);
  }, []);
  return voices;
}

function useSpeech(prefs) {
  const [speaking, setSpeaking] = React.useState(false);
  const prefsRef = React.useRef(prefs);
  prefsRef.current = prefs;

  React.useEffect(() => () => SPEECH_SUPPORTED && window.speechSynthesis.cancel(), []);

  const stop = React.useCallback(() => {
    if (!SPEECH_SUPPORTED) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, []);

  const speak = React.useCallback((text) => {
    if (!SPEECH_SUPPORTED || !text) return;
    const p = prefsRef.current || {};
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "pt-BR";
    utterance.rate = p.voice_rate || 1.0;
    utterance.volume = p.voice_volume == null ? 1.0 : p.voice_volume;
    const voice = pickVoice(p.voice_name);
    if (voice) utterance.voice = voice;
    utterance.onend = utterance.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  }, []);

  return { speaking, speak, stop };
}

let alertAudioContext = null;

function playAlertTone(volume = 0.6) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  alertAudioContext = alertAudioContext || new Ctx();
  const ctx = alertAudioContext;
  [0, 0.22].forEach((offset, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = i ? 660 : 880;
    const t = ctx.currentTime + offset;
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(0.25 * volume, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.2);
  });
}

function showAlertNotification(title, body) {
  if (!NOTIFICATIONS_SUPPORTED || Notification.permission !== "granted") return;
  const n = new Notification(title, { body, icon: "/devkit/logo_sem_fundo.png", tag: "sentinela-alert" });
  n.onclick = () => {
    window.focus();
    n.close();
  };
}
