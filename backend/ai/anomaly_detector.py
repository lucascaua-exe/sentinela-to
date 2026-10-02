"""
Sentinela TO - Motor de Detecção de Anomalias Elétricas

Abordagem híbrida:
  1. IA não supervisionada (Isolation Forest) treinada com o perfil de operação
     normal da planta: detecta desvios multivariados sutis, inclusive padrões
     que nenhum limiar isolado capturaria (ex.: degradação lenta pré-falha).
  2. Janela móvel de confirmação: evita disparos por ruído/transitórios curtos.
  3. Camada de diagnóstico baseada nas faixas regulatórias da ANEEL (PRODIST
     Módulo 8) para explicar a causa ao operador e escolher a ação de proteção.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import IsolationForest

NOMINAL_VOLTAGE = 220.0
NOMINAL_FREQUENCY = 60.0
RATED_CURRENT = 45.0

# PRODIST Módulo 8 - tensão de atendimento para Vn = 220 V
V_ADEQUATE = (202.0, 231.0)
V_PRECARIOUS = (191.0, 233.0)
V_INTERRUPTION = 50.0
IMBALANCE_LIMIT_PCT = 3.0
THD_LIMIT_PCT = 10.0
FREQ_LIMITS = (59.5, 60.5)
FREQ_CRITICAL = (58.5, 61.5)
POWER_FACTOR_REF = 0.92

FEATURE_NAMES = (
    "voltage_a",
    "voltage_b",
    "voltage_c",
    "current_total",
    "frequency",
    "power_factor",
    "thd_voltage",
    "imbalance_pct",
    "max_deviation_pct",
)

SEVERITY_ORDER = {"NORMAL": 0, "INFO": 1, "WARNING": 2, "CRITICAL": 3}


@dataclass
class Diagnostic:
    code: str
    level: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message}


@dataclass
class Analysis:
    risk_score: float
    is_anomaly: bool
    window_ratio: float
    severity: str
    headline: str
    recommended_action: str
    diagnosis: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)
    affected_phases: list[str] = field(default_factory=list)
    imbalance_pct: float = 0.0
    max_deviation_pct: float = 0.0
    latency_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "risk_score": self.risk_score,
            "is_anomaly": self.is_anomaly,
            "window_ratio": self.window_ratio,
            "severity": self.severity,
            "headline": self.headline,
            "recommended_action": self.recommended_action,
            "diagnosis": self.diagnosis,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "affected_phases": self.affected_phases,
            "imbalance_pct": self.imbalance_pct,
            "max_deviation_pct": self.max_deviation_pct,
            "latency_ms": self.latency_ms,
        }


def voltage_imbalance_pct(v_a: float, v_b: float, v_c: float) -> float:
    """Desequilíbrio de tensão (método NEMA): desvio máximo em relação à média."""
    avg = (v_a + v_b + v_c) / 3.0
    if avg < 1.0:
        return 0.0
    return max(abs(v_a - avg), abs(v_b - avg), abs(v_c - avg)) / avg * 100.0


def phase_voltages(sample: dict) -> dict[str, float]:
    return {"A": sample["voltage_a"], "B": sample["voltage_b"], "C": sample["voltage_c"]}


def extract_features(sample: dict) -> np.ndarray:
    v = (sample["voltage_a"], sample["voltage_b"], sample["voltage_c"])
    imbalance = voltage_imbalance_pct(*v)
    max_dev = max(abs(x - NOMINAL_VOLTAGE) for x in v) / NOMINAL_VOLTAGE * 100.0
    return np.array(
        [
            v[0],
            v[1],
            v[2],
            sample["current_total"],
            sample["frequency"],
            sample["power_factor"],
            sample.get("thd_voltage", 3.0),
            imbalance,
            max_dev,
        ],
        dtype=float,
    )


def generate_baseline(n_samples: int, rng: np.random.Generator) -> np.ndarray:
    """Perfil de operação normal usado na calibração inicial do modelo.

    Em campo, este baseline é substituído pelas primeiras semanas de medição
    real da própria planta (aprendizado do "DNA elétrico" da fábrica).
    """
    v_a = rng.uniform(213.0, 227.0, n_samples)
    v_b = rng.uniform(212.0, 226.0, n_samples)
    v_c = rng.uniform(213.0, 228.0, n_samples)
    current = rng.uniform(22.0, 42.0, n_samples)
    freq = rng.uniform(59.85, 60.15, n_samples)
    pf = rng.uniform(0.92, 0.98, n_samples)
    thd = rng.uniform(1.5, 5.0, n_samples)
    rows = [
        extract_features(
            {
                "voltage_a": v_a[i],
                "voltage_b": v_b[i],
                "voltage_c": v_c[i],
                "current_total": current[i],
                "frequency": freq[i],
                "power_factor": pf[i],
                "thd_voltage": thd[i],
            }
        )
        for i in range(n_samples)
    ]
    return np.vstack(rows)


class AnomalyDetector:
    def __init__(
        self,
        window_size: int = 6,
        rule_persistence: int = 3,
        n_baseline: int = 4000,
        contamination: float = 0.01,
        seed: int = 42,
        model: IsolationForest | None = None,
    ) -> None:
        if model is None:
            rng = np.random.default_rng(seed)
            model = IsolationForest(n_estimators=150, contamination=contamination, random_state=seed)
            model.fit(generate_baseline(n_baseline, rng))
        # O modelo treinado é somente leitura e pode ser compartilhado entre unidades; a janela é por unidade.
        self.model = model
        self.window: deque[tuple[bool, float]] = deque(maxlen=window_size)
        # Uma regra regulatória só eleva a severidade se persistir nas últimas N amostras.
        self.rule_levels: deque[int] = deque(maxlen=rule_persistence)
        self.samples_processed = 0

    @staticmethod
    def _risk_from_decision(decision: float) -> float:
        # decision_function > 0 => normal; < 0 => anômalo. Mapeado para 0-100.
        return float(np.clip(50.0 - decision * 400.0, 0.0, 100.0))

    def reset_window(self) -> None:
        self.window.clear()
        self.rule_levels.clear()

    def analyze(self, sample: dict) -> Analysis:
        started = time.perf_counter()
        features = extract_features(sample)
        decision = float(self.model.decision_function(features.reshape(1, -1))[0])
        risk = round(self._risk_from_decision(decision), 1)
        is_anomaly = risk >= 50.0

        self.window.append((is_anomaly, risk))
        flags = [f for f, _ in self.window]
        window_ratio = sum(flags) / len(flags)
        window_risk = float(np.mean([r for _, r in self.window]))

        diagnostics, phases = self._diagnose(sample, features)
        self.rule_levels.append(max((SEVERITY_ORDER[d.level] for d in diagnostics), default=0))
        # Até a persistência ser observável (após iniciar ou reiniciar a janela), regras não elevam a severidade.
        warmed_up = len(self.rule_levels) == self.rule_levels.maxlen
        rule_level = min(self.rule_levels) if warmed_up else 0

        severity = self._classify(rule_level, window_ratio, window_risk, diagnostics, is_anomaly and warmed_up)
        headline, action, diagnosis = self._compose_message(severity, diagnostics, phases, sample)
        self.samples_processed += 1

        return Analysis(
            risk_score=risk,
            is_anomaly=is_anomaly,
            window_ratio=round(window_ratio, 2),
            severity=severity,
            headline=headline,
            recommended_action=action,
            diagnosis=diagnosis,
            diagnostics=diagnostics,
            affected_phases=phases,
            imbalance_pct=round(float(features[7]), 2),
            max_deviation_pct=round(float(features[8]), 2),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )

    @staticmethod
    def _classify(
        rule_level: int, window_ratio: float, window_risk: float, diagnostics: list[Diagnostic], is_anomaly: bool
    ) -> str:
        if any(d.code == "INTERRUPTION" for d in diagnostics):
            return "CRITICAL"
        # A janela confirma a persistência, mas o alerta só se mantém se a amostra atual ainda for anômala.
        ai_confirmed = window_ratio >= 0.5 and is_anomaly
        if ai_confirmed and (rule_level >= SEVERITY_ORDER["CRITICAL"] or window_risk >= 85.0):
            return "CRITICAL"
        if ai_confirmed or rule_level >= SEVERITY_ORDER["WARNING"]:
            return "WARNING"
        if window_ratio > 0 or rule_level == SEVERITY_ORDER["INFO"]:
            return "INFO"
        return "NORMAL"

    @staticmethod
    def _diagnose(sample: dict, features: np.ndarray) -> tuple[list[Diagnostic], list[str]]:
        diags: list[Diagnostic] = []
        phases = phase_voltages(sample)

        if all(v < V_INTERRUPTION for v in phases.values()):
            diags.append(Diagnostic("INTERRUPTION", "CRITICAL", "Interrupção total do fornecimento da concessionária."))
            return diags, list(phases)

        lost = [p for p, v in phases.items() if v < V_INTERRUPTION]
        if lost:
            diags.append(Diagnostic("PHASE_LOSS", "CRITICAL", f"Queda da(s) fase(s) {', '.join(lost)} (0 V): operação monofásica dos motores."))

        sag_crit = [p for p, v in phases.items() if V_INTERRUPTION <= v < V_PRECARIOUS[0]]
        sag_warn = [p for p, v in phases.items() if V_PRECARIOUS[0] <= v < V_ADEQUATE[0]]
        swell_crit = [p for p, v in phases.items() if v > V_PRECARIOUS[1]]
        swell_warn = [p for p, v in phases.items() if V_ADEQUATE[1] < v <= V_PRECARIOUS[1]]

        if sag_crit:
            drop = max((NOMINAL_VOLTAGE - phases[p]) / NOMINAL_VOLTAGE * 100 for p in sag_crit)
            diags.append(Diagnostic("SAG", "CRITICAL", f"Afundamento de tensão crítico na(s) fase(s) {', '.join(sag_crit)} (-{drop:.0f}%)."))
        if sag_warn:
            diags.append(Diagnostic("SAG", "WARNING", f"Tensão em faixa precária na(s) fase(s) {', '.join(sag_warn)}."))
        if swell_crit:
            rise = max((phases[p] - NOMINAL_VOLTAGE) / NOMINAL_VOLTAGE * 100 for p in swell_crit)
            diags.append(Diagnostic("SWELL", "CRITICAL", f"Sobretensão crítica na(s) fase(s) {', '.join(swell_crit)} (+{rise:.0f}%)."))
        if swell_warn:
            diags.append(Diagnostic("SWELL", "WARNING", f"Tensão acima da faixa adequada na(s) fase(s) {', '.join(swell_warn)}."))

        imbalance = float(features[7])
        if imbalance > 2 * IMBALANCE_LIMIT_PCT:
            diags.append(Diagnostic("IMBALANCE", "CRITICAL", f"Desequilíbrio de tensão severo ({imbalance:.1f}%, limite {IMBALANCE_LIMIT_PCT:.0f}%)."))
        elif imbalance > IMBALANCE_LIMIT_PCT:
            diags.append(Diagnostic("IMBALANCE", "WARNING", f"Desequilíbrio de tensão acima do limite ({imbalance:.1f}%)."))

        current = sample["current_total"]
        if current > RATED_CURRENT * 1.25:
            diags.append(Diagnostic("OVERCURRENT", "CRITICAL", f"Sobrecorrente de {current / RATED_CURRENT * 100 - 100:.0f}% acima da nominal."))
        elif current > RATED_CURRENT * 1.1:
            diags.append(Diagnostic("OVERCURRENT", "WARNING", f"Corrente {current:.1f} A acima da nominal ({RATED_CURRENT:.0f} A)."))

        freq = sample["frequency"]
        if not FREQ_CRITICAL[0] <= freq <= FREQ_CRITICAL[1]:
            diags.append(Diagnostic("FREQUENCY", "CRITICAL", f"Frequência fora dos limites críticos ({freq:.2f} Hz)."))
        elif not FREQ_LIMITS[0] <= freq <= FREQ_LIMITS[1]:
            diags.append(Diagnostic("FREQUENCY", "WARNING", f"Desvio de frequência ({freq:.2f} Hz)."))

        thd = sample.get("thd_voltage", 0.0)
        if thd > THD_LIMIT_PCT:
            diags.append(Diagnostic("HARMONIC", "WARNING", f"Distorção harmônica total {thd:.1f}% (limite PRODIST {THD_LIMIT_PCT:.0f}%)."))

        if sample["power_factor"] < POWER_FACTOR_REF:
            diags.append(Diagnostic("POWER_FACTOR", "INFO", f"Fator de potência {sample['power_factor']:.2f} abaixo de 0,92: risco de cobrança de reativo excedente."))

        affected = sorted(set(lost + sag_crit + sag_warn + swell_crit + swell_warn))
        return diags, affected

    @staticmethod
    def _compose_message(severity: str, diagnostics: list[Diagnostic], phases: list[str], sample: dict) -> tuple[str, str, str]:
        """Retorna (manchete do alerta, ação recomendada, diagnóstico curto para o operador)."""
        codes = {d.code for d in diagnostics}
        critical = severity == "CRITICAL"
        v = phase_voltages(sample)

        if severity in ("NORMAL", "INFO"):
            return (
                "Rede elétrica estável. Parâmetros dentro da faixa adequada.",
                "Nenhuma ação necessária.",
                "Rede operando em conformidade com os padrões da ANEEL.",
            )

        if "INTERRUPTION" in codes:
            headline = "Interrupção total do fornecimento da concessionária. Linhas de produção em risco de parada."
            action = "Transferir cargas críticas para geração solar + banco de baterias."
            diagnosis = "Interrupção total do fornecimento da concessionária."
        elif "PHASE_LOSS" in codes:
            lost = [p for p, volts in v.items() if volts < V_INTERRUPTION]
            headline = f"Queda da Fase {', '.join(lost)} (0 V). Desequilíbrio crítico trifásico: motores em operação monofásica."
            action = "Transferir cargas críticas para autoprodução imediatamente."
            diagnosis = "Desequilíbrio crítico trifásico."
        elif "SAG" in codes:
            low = [p for p in phases if v[p] < NOMINAL_VOLTAGE]
            drop = max((NOMINAL_VOLTAGE - v[p]) / NOMINAL_VOLTAGE * 100 for p in low) if low else 0
            if len(low) == 1:
                label = f"na Fase {low[0]}"
            else:
                label = f"nas Fases {', '.join(low[:-1])} e {low[-1]}"
            headline = f"Queda de tensão {label} (>{int(drop)}%)."
            if "OVERCURRENT" in codes:
                headline += " Risco de queima por sobrecorrente nos compressores e motores."
            action = "Isolar cargas sensíveis e comutar para autoprodução."
            diagnosis = (
                "Subtensão severa identificada no QGD. Risco iminente de sobrecorrente nos compressores."
                if critical
                else "Subtensão identificada no QGD."
            )
        elif "SWELL" in codes:
            headline = "Sobretensão na rede de entrada. Risco de ruptura de isolamento em motores e inversores."
            action = "Isolar a planta da rede e comutar para autoprodução."
            diagnosis = (
                "Sobretensão prejudicial isolada. Ativando desacoplamento de proteção."
                if critical
                else "Sobretensão identificada na entrada."
            )
        elif "IMBALANCE" in codes:
            headline = "Desequilíbrio severo entre fases. Risco de sobreaquecimento de motores trifásicos."
            action = "Verificar falta de fase e reduzir carga dos motores."
            diagnosis = "Desequilíbrio entre fases acima do limite da ANEEL."
        elif "HARMONIC" in codes:
            headline = "Distorção harmônica elevada. Aquecimento de transformadores e falhas em CLPs/inversores."
            action = "Avaliar filtros harmônicos e cargas não lineares."
            diagnosis = "Distorção harmônica acima do limite da ANEEL."
        elif "FREQUENCY" in codes:
            headline = "Instabilidade de frequência na rede da concessionária."
            action = "Preparar comutação para autoprodução."
            diagnosis = "Instabilidade de frequência na rede."
        else:
            headline = "Padrão elétrico anômalo detectado pela IA (assinatura pré-falha)."
            action = "Inspecionar QGD e preparar contingência."
            diagnosis = "Padrão pré-falha identificado pela IA."

        prefix = "ALERTA CRÍTICO" if critical else "ATENÇÃO"
        return f"{prefix}: {headline}", action, diagnosis
