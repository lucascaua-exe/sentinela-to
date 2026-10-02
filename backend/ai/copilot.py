"""
Sentinela TO - Copilot de Engenharia de Qualidade de Energia (RAG)

Pipeline:
  1. Monta a consulta a partir dos códigos de diagnóstico e das grandezas medidas.
  2. Recupera os trechos técnicos mais relevantes da base de conhecimento (TF-IDF).
  3. Gera o diagnóstico estruturado:
     - com LLM (API compatível com OpenAI) quando OPENAI_API_KEY está definida;
     - caso contrário, com o compositor local, que monta causa, ações e referências
       exclusivamente a partir dos trechos recuperados.

As referências normativas exibidas ao operador são sempre restritas aos trechos
recuperados, inclusive quando o texto é gerado pelo LLM.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from backend.ai.anomaly_detector import (
    FREQ_LIMITS,
    NOMINAL_VOLTAGE,
    POWER_FACTOR_REF,
    RATED_CURRENT,
    SEVERITY_ORDER,
    THD_LIMIT_PCT,
    V_ADEQUATE,
    V_INTERRUPTION,
    phase_voltages,
    voltage_imbalance_pct,
)
from backend.ai.knowledge_base import KnowledgeBase, Retrieved

log = logging.getLogger("sentinela.copilot")

TOP_K = 6
CODES_SEARCHED = 3
HITS_PER_CODE = 3
LLM_TIMEOUT_S = 20.0

# Índice de confiabilidade: CS = 0,7·S_vetor + 0,3·S_densidade.
CONFIDENCE_THRESHOLD = 90.0
W_VECTOR = 0.7
W_DENSITY = 0.3
PRIMARY_WEIGHT = 0.6
MIN_SIMILARITY = 0.05
NEIGHBORS = 3
DENSITY_FULL = 3
# Uma ocorrência validada em campo vale por duas fontes bibliográficas (no voto vetorial e na densidade).
VALIDATED_WEIGHT = 2
CONTINGENCY_IMMEDIATE = (
    "Tratar o diagnóstico como hipótese e isolar por precaução o barramento das cargas sensíveis até a validação."
)
CONTINGENCY_ESCALATE = "Acionar a engenharia para validar a ocorrência registrada na fila de curadoria."
CODE_TERMS = {
    "INTERRUPTION": "interrupção falta de energia apagão religamento",
    "PHASE_LOSS": "falta de fase monofasamento fusível queima motor",
    "SAG": "afundamento de tensão subtensão sag motor",
    "SWELL": "elevação de tensão sobretensão swell surto DPS",
    "IMBALANCE": "desequilíbrio de tensão entre fases sequência negativa",
    "OVERCURRENT": "sobrecorrente motor relé térmico",
    "FREQUENCY": "variação de frequência subfrequência",
    "HARMONIC": "distorção harmônica THD cargas não lineares",
    "POWER_FACTOR": "fator de potência reativo capacitor",
    "AI_PATTERN": "padrão pré-falha degradação tendência manutenção preditiva",
}
# Sintomas que, junto com estes distúrbios, são consequência deles e não causa raiz.
DERIVED_FROM = {
    "IMBALANCE": {"PHASE_LOSS", "SAG", "SWELL", "INTERRUPTION"},
    "FREQUENCY": {"INTERRUPTION"},
    "OVERCURRENT": {"INTERRUPTION"},
}
SEVERITY_LABEL = {"CRITICAL": "CRÍTICA", "WARNING": "MÉDIA", "INFO": "BAIXA", "NORMAL": "BAIXA"}
SPEECH_PREFIX = {"CRÍTICA": "Alerta crítico", "ALTA": "Alerta", "MÉDIA": "Atenção", "BAIXA": "Aviso"}

SYSTEM_PROMPT = """Você é o Copilot de Engenharia de Qualidade de Energia do sistema Sentinela TO, um especialista sênior em infraestrutura elétrica industrial.

Sua tarefa é analisar os dados de telemetria recebidos e o contexto técnico resgatado da base de conhecimento para diagnosticar a anomalia e sugerir soluções rigorosas e de procedência comprovada.
Use somente o contexto técnico fornecido. Cite em norm_references apenas referências que aparecem entre colchetes no contexto.

DADOS DE TELEMETRIA ATUAIS:
- Tensão Fase A: {voltage_a}V | Fase B: {voltage_b}V | Fase C: {voltage_c}V
- Frequência: {frequency}Hz | Fator de Potência: {power_factor} | THD-V: {thd_v}%
- Corrente total: {current}A | Diagnóstico das regras: {codes}
- Localização: {location}

CONTEXTO TÉCNICO RESGATADO (RAG):
{retrieved_documents}

FORMATO DA RESPOSTA (Retornar estritamente em JSON):
{{
  "cause_analysis": "Explicação técnica sucinta sobre a causa raiz provável baseada nos dados e normas.",
  "severity_level": "BAIXA" | "MÉDIA" | "ALTA" | "CRÍTICA",
  "confidence_score": 0-100,
  "norm_references": ["referência exatamente como aparece no contexto"],
  "recommended_solutions": [
    {{"step": 1, "action": "Ação imediata de contenção.", "type": "IMEDIATA"}},
    {{"step": 2, "action": "Ação de médio prazo.", "type": "PREVENTIVA"}}
  ],
  "speech_summary": "Resumo de 2 frases em tom calmo, claro e profissional projetado para ser lido por sintetizador de voz para o operador."
}}"""


class Solution(BaseModel):
    step: int
    action: str
    type: Literal["IMEDIATA", "PREVENTIVA"]
    contingency: bool = False


class Diagnosis(BaseModel):
    cause_analysis: str
    severity_level: Literal["BAIXA", "MÉDIA", "ALTA", "CRÍTICA"]
    confidence_score: float = Field(ge=0, le=100)
    norm_references: list[str]
    recommended_solutions: list[Solution] = Field(min_length=1)
    speech_summary: str


@dataclass
class DiagnosisRequest:
    site_id: str
    city: str
    location: str
    sample: dict
    severity: str
    codes: list[str]
    risk_score: float = 0.0
    affected_phases: list[str] = field(default_factory=list)

    @property
    def primary_code(self) -> str:
        return self.codes[0] if self.codes else "AI_PATTERN"


def ordered_codes(diagnostics: list[dict], severity: str) -> list[str]:
    """Códigos do mais grave ao mais leve; sintomas derivados vão para o fim e anomalia só da IA vira AI_PATTERN."""
    ranked = sorted(diagnostics, key=lambda d: SEVERITY_ORDER[d["level"]], reverse=True)
    codes = list(dict.fromkeys(d["code"] for d in ranked))
    level = {}
    for d in ranked:
        level.setdefault(d["code"], SEVERITY_ORDER[d["level"]])
    if {"SAG", "SWELL", "IMBALANCE"} <= level.keys():
        # Uma fase acima e outra abaixo ao mesmo tempo é assinatura de desequilíbrio (deslocamento de neutro).
        derived = [c for c in codes if c in ("SAG", "SWELL")]
    else:
        # Só é sintoma derivado se o distúrbio de origem for pelo menos tão grave quanto ele.
        derived = [c for c in codes if any(level.get(p, -1) >= level[c] for p in DERIVED_FROM.get(c, ()))]
    codes = [c for c in codes if c not in derived] + derived
    top = SEVERITY_ORDER[ranked[0]["level"]] if ranked else 0
    if top < SEVERITY_ORDER[severity]:
        codes.insert(0, "AI_PATTERN")
    return codes or ["AI_PATTERN"]


def _num(value: float, decimals: int = 0) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def measured_facts(sample: dict) -> str:
    voltages = phase_voltages(sample)
    if all(v < V_INTERRUPTION for v in voltages.values()):
        return "tensão abaixo de 10% da nominal nas três fases"
    facts = []
    off = [
        f"{phase} em {_num(v)} V ({'+' if v >= NOMINAL_VOLTAGE else '−'}{_num(abs(v - NOMINAL_VOLTAGE) / NOMINAL_VOLTAGE * 100)}%)"
        for phase, v in phase_voltages(sample).items()
        if not V_ADEQUATE[0] <= v <= V_ADEQUATE[1]
    ]
    if off:
        facts.append(("fase " if len(off) == 1 else "fases ") + ", ".join(off))
    imbalance = voltage_imbalance_pct(*voltages.values())
    if imbalance > 2.0 and min(voltages.values()) >= V_INTERRUPTION:
        facts.append(f"desequilíbrio de {_num(imbalance, 1)}%")
    if not FREQ_LIMITS[0] <= sample["frequency"] <= FREQ_LIMITS[1]:
        facts.append(f"frequência de {_num(sample['frequency'], 2)} Hz")
    if sample["current_total"] > RATED_CURRENT * 1.1:
        facts.append(f"corrente de {_num(sample['current_total'], 1)} A")
    if sample.get("thd_voltage", 0.0) > THD_LIMIT_PCT * 0.8:
        facts.append(f"THD-V de {_num(sample['thd_voltage'], 1)}%")
    if sample["power_factor"] < POWER_FACTOR_REF:
        facts.append(f"fator de potência {_num(sample['power_factor'], 2)}")
    return "; ".join(facts)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


class DiagnosticCopilot:
    def __init__(self, kb: KnowledgeBase | None = None) -> None:
        self.kb = kb or KnowledgeBase()

    @property
    def llm_model(self) -> str | None:
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        return os.environ.get("SENTINELA_LLM_MODEL", "gpt-4o-mini")

    @property
    def engine(self) -> str:
        return f"llm:{self.llm_model}" if self.llm_model else "local"

    @staticmethod
    def _query(req: DiagnosisRequest, code: str) -> str:
        return f"{code} {CODE_TERMS.get(code, code)} {req.city}"

    def retrieve(self, req: DiagnosisRequest) -> list[Retrieved]:
        """Busca por código, do mais grave ao mais leve: a causa raiz não é diluída pelos sintomas secundários."""
        merged: dict[str, Retrieved] = {}
        for code in req.codes[:CODES_SEARCHED]:
            for hit in self.kb.search(self._query(req, code), k=HITS_PER_CODE, codes={code}):
                merged.setdefault(hit.chunk.id, hit)
        return list(merged.values())[:TOP_K]

    def evidence(self, req: DiagnosisRequest, retrieved: list[Retrieved]) -> dict:
        """
        S_vetor: para cada distúrbio, a fração (ponderada pelo cosseno) dos vizinhos mais próximos no
        espaço vetorial que são evidência marcada para ele. O cosseno TF-IDF bruto varia com o vocabulário
        da consulta, por isso a medida é relativa. A causa raiz pesa 60%; os demais dividem o restante.
        S_densidade: fontes independentes da base com evidência relevante para a causa raiz.
        """
        codes = req.codes[:CODES_SEARCHED] or ["AI_PATTERN"]
        weights = [PRIMARY_WEIGHT] + [(1 - PRIMARY_WEIGHT) / (len(codes) - 1)] * (len(codes) - 1) if len(codes) > 1 else [1.0]
        s_vector = 0.0
        doc_sources: set[str] = set()
        validated = 0
        for position, (code, weight) in enumerate(zip(codes, weights)):
            chunks, raw = self.kb.similarities(self._query(req, code))
            neighbors = [i for i in raw.argsort()[::-1][:NEIGHBORS] if raw[i] >= MIN_SIMILARITY]
            votes = {i: float(raw[i]) * (VALIDATED_WEIGHT if chunks[i].validated else 1) for i in neighbors}
            total = sum(votes.values())
            supporting = sum(v for i, v in votes.items() if code in chunks[i].tags)
            s_vector += weight * (supporting / total if total else 0.0)
            if position == 0:
                relevant = [c for i, c in enumerate(chunks) if code in c.tags and raw[i] >= MIN_SIMILARITY]
                doc_sources = {c.source for c in relevant if not c.validated}
                validated = sum(1 for c in relevant if c.validated)
        s_density = min(1.0, (len(doc_sources) + VALIDATED_WEIGHT * validated) / DENSITY_FULL)

        score = min(99.9, 100.0 * (W_VECTOR * s_vector + W_DENSITY * s_density))
        return {
            "score": round(score, 1),
            "vector": round(s_vector, 3),
            "density": round(s_density, 3),
            "sources": len(doc_sources),
            "validated": validated,
        }

    @staticmethod
    def severity_level(req: DiagnosisRequest) -> str:
        if req.severity == "WARNING" and req.risk_score >= 70:
            return "ALTA"
        return SEVERITY_LABEL[req.severity]

    def compose_local(self, req: DiagnosisRequest, retrieved: list[Retrieved]) -> Diagnosis:
        # Resoluções validadas pela engenharia têm precedência sobre a literatura genérica.
        chunks = sorted((r.chunk for r in retrieved), key=lambda c: not c.validated)
        used: list[str] = []

        causes = []
        for code in req.codes:
            match = next((c for c in chunks if code in c.tags and c.cause and c.cause not in causes), None)
            if match:
                causes.append(match.cause)
                used.append(match.ref)
            if len(causes) == 2:
                break
        if not causes:
            causes.append("Padrão elétrico fora do perfil normal da planta identificado pela IA.")

        facts = measured_facts(req.sample)
        cause_analysis = (f"Medição: {facts}. " if facts else "") + " ".join(causes)

        solutions: list[Solution] = []
        for kind, attr, limit in (("IMEDIATA", "immediate", 2), ("PREVENTIVA", "preventive", 2)):
            picked = 0
            for chunk in chunks:
                for action in getattr(chunk, attr):
                    if picked < limit and all(s.action != action for s in solutions):
                        solutions.append(Solution(step=len(solutions) + 1, action=action, type=kind))
                        used.append(chunk.ref)
                        picked += 1
        if not any(s.type == "IMEDIATA" for s in solutions):
            solutions.insert(0, Solution(step=1, action="Inspecionar o QGD e preparar a contingência.", type="IMEDIATA"))
            for i, s in enumerate(solutions, start=1):
                s.step = i

        level = self.severity_level(req)
        first_action = next(s.action for s in solutions if s.type == "IMEDIATA")
        speech = (
            f"{SPEECH_PREFIX[level]} na unidade {req.city}: {_lower_first(causes[0])} "
            f"Recomendação imediata: {_lower_first(first_action)}"
        )
        references = list(dict.fromkeys(used + [c.ref for c in chunks]))[:4]
        return Diagnosis(
            cause_analysis=cause_analysis,
            severity_level=level,
            confidence_score=0.0,
            norm_references=references,
            recommended_solutions=solutions,
            speech_summary=speech,
        )

    def _prompt(self, req: DiagnosisRequest, retrieved: list[Retrieved]) -> str:
        s = req.sample
        return SYSTEM_PROMPT.format(
            voltage_a=_num(s["voltage_a"], 1),
            voltage_b=_num(s["voltage_b"], 1),
            voltage_c=_num(s["voltage_c"], 1),
            frequency=_num(s["frequency"], 2),
            power_factor=_num(s["power_factor"], 2),
            thd_v=_num(s.get("thd_voltage", 0.0), 1),
            current=_num(s["current_total"], 1),
            codes=", ".join(req.codes),
            location=f"{req.location} ({req.city})",
            retrieved_documents="\n".join(r.chunk.as_context() for r in retrieved),
        )

    async def _generate_llm(self, req: DiagnosisRequest, retrieved: list[Retrieved]) -> Diagnosis:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        payload = {
            "model": self.llm_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._prompt(req, retrieved)},
                {"role": "user", "content": "Gere o diagnóstico em JSON."},
            ],
        }
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json=payload,
            )
            resp.raise_for_status()
        diagnosis = Diagnosis.model_validate(json.loads(resp.json()["choices"][0]["message"]["content"]))

        allowed = [r.chunk.ref for r in retrieved]
        cited = [ref for ref in diagnosis.norm_references if ref in allowed]
        # A severidade vem do detector e a confiança é recalculada pela recuperação, não pela autoavaliação do LLM.
        return diagnosis.model_copy(
            update={"norm_references": cited or allowed[:4], "severity_level": self.severity_level(req)}
        )

    @staticmethod
    def _contingency(req: DiagnosisRequest, diagnosis: Diagnosis, hypothesis: str) -> Diagnosis:
        """Abaixo do limiar: o diagnóstico vira hipótese e a resposta prioriza a segurança e a validação humana."""
        score = diagnosis.confidence_score
        kept = [s.model_copy() for s in diagnosis.recommended_solutions if not s.contingency][:3]
        steps = [
            Solution(step=0, action=CONTINGENCY_IMMEDIATE, type="IMEDIATA", contingency=True),
            *kept,
            Solution(step=0, action=CONTINGENCY_ESCALATE, type="IMEDIATA", contingency=True),
        ]
        for i, s in enumerate(steps, start=1):
            s.step = i
        level = diagnosis.severity_level
        speech = (
            f"{SPEECH_PREFIX[level]} na unidade {req.city}: padrão com pouca correspondência na base técnica, "
            f"confiança de {round(score)} por cento. Hipótese: {_lower_first(hypothesis.split('. ')[0].rstrip('.'))}. "
            "Por precaução, isole as cargas sensíveis e acione a engenharia."
        )
        return diagnosis.model_copy(update={"recommended_solutions": steps, "speech_summary": speech})

    def _finish(self, req: DiagnosisRequest, retrieved: list[Retrieved], diagnosis: Diagnosis, engine: str) -> dict:
        ev = self.evidence(req, retrieved)
        diagnosis = diagnosis.model_copy(update={"confidence_score": ev["score"]})
        facts = measured_facts(req.sample)
        prefix = f"Medição: {facts}. " if facts else ""
        hypothesis = diagnosis.cause_analysis.removeprefix(prefix)
        low = ev["score"] < CONFIDENCE_THRESHOLD
        if low:
            diagnosis = self._contingency(req, diagnosis, hypothesis)
        return {
            "status": "ready",
            "id": uuid.uuid4().hex[:10],
            "site": req.site_id,
            "created_at": time.time(),
            "engine": engine,
            "primary_code": req.primary_code,
            "codes": req.codes,
            "measurement": facts,
            **diagnosis.model_dump(),
            "is_high_confidence": not low,
            "low_confidence": low,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "confidence_breakdown": ev,
            "hypothesis": hypothesis,
            "caveat": (
                f"Confiança de {_num(ev['score'], 1)}%, abaixo do limiar de {CONFIDENCE_THRESHOLD:.0f}%: "
                "trate como hipótese até a validação da engenharia."
            )
            if low
            else None,
            "autotrain": None,
            "kb_version": self.kb.version,
            "sources": [
                {
                    "ref": r.chunk.ref,
                    "title": r.chunk.title,
                    "source": r.chunk.source,
                    "score": round(r.score, 3),
                    "validated": r.chunk.validated,
                }
                for r in retrieved
            ],
        }

    def diagnose_local(self, req: DiagnosisRequest) -> dict:
        retrieved = self.retrieve(req)
        return self._finish(req, retrieved, self.compose_local(req, retrieved), "local")

    async def diagnose(self, req: DiagnosisRequest) -> dict:
        retrieved = self.retrieve(req)
        if self.llm_model:
            try:
                diagnosis = await self._generate_llm(req, retrieved)
                return self._finish(req, retrieved, diagnosis, self.engine)
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, ValidationError) as exc:
                log.warning("LLM indisponível (%s); usando o compositor local.", exc.__class__.__name__)
        return self._finish(req, retrieved, self.compose_local(req, retrieved), "local")
