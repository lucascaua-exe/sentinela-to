const NOMINAL_V = 220;

// PRODIST Módulo 8 para Vn = 220 V; precisa coincidir com backend/ai/anomaly_detector.py.
const VOLTAGE_LIMITS = {
  interruption: 50,
  precarious: [191, 233],
  adequate: [202, 231],
};

const PHASES = [
  { key: "voltage_a", label: "Fase A", color: "#EA580C" },
  { key: "voltage_b", label: "Fase B", color: "#2563EB" },
  { key: "voltage_c", label: "Fase C", color: "#475569" },
];

const CODE_LABELS = {
  SAG: "Afundamento de tensão",
  SWELL: "Sobretensão",
  PHASE_LOSS: "Falta de fase",
  IMBALANCE: "Desequilíbrio de tensão",
  INTERRUPTION: "Interrupção",
  OVERCURRENT: "Sobrecorrente",
  FREQUENCY: "Desvio de frequência",
  HARMONIC: "Distorção harmônica",
  POWER_FACTOR: "Fator de potência",
  AI_PATTERN: "Padrão atípico detectado pela IA",
};

const SEVERITY_LABELS = { CRITICAL: "Crítica", WARNING: "Atenção" };

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

function formatClock(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString("pt-BR");
}

function formatDateTime(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" });
}

function formatDayLabel(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Hoje";
  if (date.toDateString() === yesterday.toDateString()) return "Ontem";
  return date.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
}

function formatPercent(value, digits = 1) {
  return `${Number(value).toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
}
