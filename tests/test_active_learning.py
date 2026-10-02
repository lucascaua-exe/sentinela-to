import shutil

import pytest
from fastapi.testclient import TestClient

from backend.ai.copilot import (
    CODE_TERMS,
    CONFIDENCE_THRESHOLD,
    CONTINGENCY_ESCALATE,
    CONTINGENCY_IMMEDIATE,
    W_DENSITY,
    W_VECTOR,
    DiagnosisRequest,
    DiagnosticCopilot,
)
from backend.ai.knowledge_base import KNOWLEDGE_DIR, KnowledgeBase
from backend.ai.learning import KnowledgeCuration

DEVICE_HEADERS = {"X-Device-Token": "token-de-teste"}
# Só sobrecorrente, com tensões normais: a base não tem uma causa específica para isso.
OVERCURRENT_ONLY = {
    "voltage_a": 220.0,
    "voltage_b": 219.5,
    "voltage_c": 220.6,
    "current_total": 60.0,
    "frequency": 60.0,
    "power_factor": 0.95,
    "thd_voltage": 3.0,
}
VALIDATED_CAUSE = "Sobrecarga mecânica na esteira principal com tensões normais; motor operando acima da corrente de placa."


def request(codes, sample=OVERCURRENT_ONLY, severity="WARNING"):
    return DiagnosisRequest("gurupi", "Gurupi", "QGD 03", sample, severity, codes)


@pytest.fixture
def fresh_copilot(tmp_path):
    curation = KnowledgeCuration(tmp_path / "curadoria.db")
    return DiagnosticCopilot(KnowledgeBase()), curation


def test_confidence_combines_vector_similarity_and_evidence_density():
    copilot = DiagnosticCopilot()
    req = request(["SAG", "OVERCURRENT"])
    ev = copilot.evidence(req, copilot.retrieve(req))
    assert 0 <= ev["vector"] <= 1 and 0 <= ev["density"] <= 1
    assert ev["score"] == pytest.approx(min(99.9, 100 * (W_VECTOR * ev["vector"] + W_DENSITY * ev["density"])), abs=0.06)


def test_low_confidence_returns_safe_contingency():
    result = DiagnosticCopilot().diagnose_local(request(["OVERCURRENT"]))

    assert result["confidence_score"] < CONFIDENCE_THRESHOLD
    assert result["low_confidence"] and not result["is_high_confidence"]
    assert "abaixo do limiar de 90%" in result["caveat"]
    steps = result["recommended_solutions"]
    assert steps[0]["action"] == CONTINGENCY_IMMEDIATE and steps[0]["contingency"]
    assert steps[-1]["action"] == CONTINGENCY_ESCALATE
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    assert "confiança de" in result["speech_summary"] and "acione a engenharia" in result["speech_summary"]


def test_ai_only_pattern_is_flagged_as_knowledge_gap():
    result = DiagnosticCopilot().diagnose_local(request(["AI_PATTERN", "POWER_FACTOR"]))
    assert result["low_confidence"]


def test_validated_resolution_closes_the_gap_without_restart(fresh_copilot):
    copilot, curation = fresh_copilot
    req = request(["OVERCURRENT"])
    first = copilot.diagnose_local(req)
    gap = curation.record_gap(first, req.codes, req.sample, req.city)
    again = curation.record_gap(copilot.diagnose_local(req), req.codes, req.sample, req.city)
    assert again["id"] == gap["id"] and again["occurrences"] == 2
    assert gap["hypothesis"] and gap["suggested_immediate"]
    assert CONTINGENCY_IMMEDIATE not in gap["suggested_immediate"]

    curation.resolve(
        gap["id"],
        VALIDATED_CAUSE,
        ["Reduzir a carga da esteira e conferir o alinhamento do acoplamento."],
        ["Instalar monitoramento de corrente por motor com alarme a 105% da corrente de placa."],
        "NBR 5410 — proteção contra sobrecorrentes",
        "engenheira.ana",
    )
    version = copilot.kb.version
    assert copilot.kb.reindex(curation.validated_chunks(CODE_TERMS)) == version + 1

    retrained = copilot.diagnose_local(req)
    assert retrained["confidence_score"] >= CONFIDENCE_THRESHOLD and not retrained["low_confidence"]
    assert VALIDATED_CAUSE in retrained["cause_analysis"]
    assert any(ref.startswith("Ocorrência validada #1") for ref in retrained["norm_references"])
    assert retrained["confidence_breakdown"]["validated"] == 1
    assert curation.list_gaps("OPEN") == []
    with pytest.raises(ValueError):
        curation.resolve(gap["id"], VALIDATED_CAUSE, ["x"], [], "", "outro")


def test_new_repository_document_is_indexed_at_runtime(tmp_path):
    repo = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE_DIR, repo)
    kb = KnowledgeBase(repo)
    before = len(kb.chunks)
    assert kb.refresh_if_changed() is False

    (repo / "manual_novo.md").write_text(
        "---\nsource: Manual de teste\ncategory: Manual\n---\n\n"
        "## Sobrecarga em esteiras\nref: Manual de teste — esteiras\ntags: OVERCURRENT esteira\n"
        "Esteiras sobrecarregadas elevam a corrente dos motores.\n",
        encoding="utf-8",
    )
    assert kb.refresh_if_changed() is True
    assert len(kb.chunks) == before + 1 and kb.version == 2
    assert kb.search("OVERCURRENT esteira", k=1)[0].chunk.ref == "Manual de teste — esteiras"


def test_feedback_endpoint_retrains_the_live_engine():
    from backend.main import app, copilot, curation

    reading = OVERCURRENT_ONLY | {"site_id": "gurupi"}
    with TestClient(app) as client:
        assert client.post("/api/v1/feedback", json={"gap_id": 1}).status_code == 401
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "senha-de-teste-123"}
        ).status_code == 200
        try:
            uncertain = client.post("/api/v1/diagnose", json=reading).json()
            assert uncertain["low_confidence"]
            gap_id = uncertain["autotrain"]["gap_id"]
            listed = client.get("/api/v1/knowledge/gaps").json()
            assert [g["id"] for g in listed["gaps"]] == [gap_id]
            assert listed["kb"]["threshold"] == CONFIDENCE_THRESHOLD

            bad = {"gap_id": gap_id, "correct_cause": "curta", "immediate_actions": []}
            assert client.post("/api/v1/feedback", json=bad).status_code == 422

            feedback = {
                "gap_id": gap_id,
                "correct_cause": VALIDATED_CAUSE,
                "immediate_actions": ["Reduzir a carga da esteira e conferir o acoplamento.", "  "],
                "preventive_actions": ["Monitorar a corrente por motor com alarme a 105% da corrente de placa."],
                "norm_reference": "NBR 5410 — proteção contra sobrecorrentes",
            }
            body = client.post("/api/v1/feedback", json=feedback).json()
            assert body["status"] == "SUCCESS" and body["kb_version"] >= 2

            confident = client.post("/api/v1/diagnose", json=reading).json()
            assert confident["is_high_confidence"] and confident["autotrain"] is None
            assert VALIDATED_CAUSE in confident["cause_analysis"]
            assert client.get("/api/v1/knowledge/gaps").json()["gaps"] == []
            assert client.post("/api/v1/feedback", json=feedback).status_code == 409
            assert client.post("/api/v1/knowledge/gaps/999/dismiss").status_code == 404
        finally:
            # Os demais testes usam a base sem as resoluções validadas deste cenário.
            copilot.kb.reindex([])
            assert curation.counts()["validated"] >= 1
