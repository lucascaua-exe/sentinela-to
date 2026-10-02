import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.ai.anomaly_detector import AnomalyDetector
from backend.ai.copilot import DiagnosisRequest, DiagnosticCopilot, ordered_codes
from backend.ai.knowledge_base import KnowledgeBase
from backend.core.transfer_switch import TransferSwitch
from simulator.iot_emulator import generate_electrical_data

DEVICE_HEADERS = {"X-Device-Token": "token-de-teste"}


@pytest.fixture(scope="module")
def copilot():
    return DiagnosticCopilot()


@pytest.fixture(scope="module")
def detector():
    return AnomalyDetector()


def scenario_request(detector, mode, n=12):
    detector.reset_window()
    for tick in range(n):
        sample = generate_electrical_data(mode, tick)
        analysis = detector.analyze(sample)
    diagnostics = [d.as_dict() for d in analysis.diagnostics]
    return DiagnosisRequest(
        site_id="palmas",
        city="Palmas",
        location="Distrito Industrial de Palmas - Setor 02",
        sample=sample,
        severity=analysis.severity,
        codes=ordered_codes(diagnostics, analysis.severity),
        risk_score=analysis.risk_score,
    )


def test_knowledge_base_covers_all_required_sources():
    kb = KnowledgeBase()
    joined = " ".join(kb.sources)
    for name in ("IEEE", "NBR 5410", "PRODIST", "WEG", "regionais"):
        assert name in joined
    assert all(chunk.ref and chunk.text for chunk in kb.chunks)


@pytest.mark.parametrize(
    "mode, primary, expected_ref",
    [
        ("SAG_PALMAS", "SAG", "IEEE 1159 — variações de curta duração: afundamento"),
        ("SWELL", "SWELL", "IEEE 1159 — variações de curta duração: elevação"),
        ("PHASE_LOSS", "PHASE_LOSS", "Boas práticas de fabricantes (WEG, Schneider, ABB) — falta de fase"),
        ("PHASE_IMBALANCE", "IMBALANCE", "IEEE 1159 — desequilíbrio de tensão"),
        ("HARMONIC", "HARMONIC", "IEEE 1159 — distorção da forma de onda"),
        ("BLACKOUT", "INTERRUPTION", "IEEE 1159 — interrupções"),
    ],
)
def test_diagnosis_is_grounded_in_retrieved_sources(copilot, detector, mode, primary, expected_ref):
    req = scenario_request(detector, mode)
    result = copilot.diagnose_local(req)

    assert result["primary_code"] == primary
    retrieved_refs = {s["ref"] for s in result["sources"]}
    assert set(result["norm_references"]) <= retrieved_refs
    assert expected_ref in result["norm_references"]
    assert any(s["type"] == "IMEDIATA" for s in result["recommended_solutions"])
    assert [s["step"] for s in result["recommended_solutions"]] == list(range(1, len(result["recommended_solutions"]) + 1))
    assert result["cause_analysis"].startswith("Medição:")
    assert "Palmas" in result["speech_summary"] and "Recomendação imediata" in result["speech_summary"]
    assert result["confidence_score"] >= 90 and result["is_high_confidence"]
    assert not any(s["contingency"] for s in result["recommended_solutions"])


def test_derived_symptoms_do_not_become_root_cause():
    sag_with_imbalance = [
        {"code": "SAG", "level": "CRITICAL"},
        {"code": "IMBALANCE", "level": "CRITICAL"},
        {"code": "POWER_FACTOR", "level": "INFO"},
    ]
    assert ordered_codes(sag_with_imbalance, "CRITICAL")[0] == "SAG"

    severe_imbalance = [{"code": "SAG", "level": "WARNING"}, {"code": "IMBALANCE", "level": "CRITICAL"}]
    assert ordered_codes(severe_imbalance, "CRITICAL")[0] == "IMBALANCE"

    one_phase_up_one_down = [
        {"code": "SAG", "level": "WARNING"},
        {"code": "SWELL", "level": "CRITICAL"},
        {"code": "IMBALANCE", "level": "CRITICAL"},
    ]
    assert ordered_codes(one_phase_up_one_down, "CRITICAL")[0] == "IMBALANCE"

    assert ordered_codes([{"code": "POWER_FACTOR", "level": "INFO"}], "WARNING")[0] == "AI_PATTERN"


def test_llm_answer_cannot_cite_sources_outside_the_retrieval(copilot, detector, monkeypatch):
    req = scenario_request(detector, "SAG_PALMAS")
    retrieved = copilot.retrieve(req)
    valid_ref = retrieved[0].chunk.ref

    def handler(request: httpx.Request) -> httpx.Response:
        assert valid_ref in json.loads(request.content)["messages"][0]["content"]
        answer = {
            "cause_analysis": "Afundamento originado na rede.",
            "severity_level": "BAIXA",
            "confidence_score": 100,
            "norm_references": [valid_ref, "IEEE 9999 — norma inventada"],
            "recommended_solutions": [{"step": 1, "action": "Isolar cargas.", "type": "IMEDIATA"}],
            "speech_summary": "Afundamento na rede. Isolar cargas.",
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "backend.ai.copilot.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    result = asyncio.run(copilot.diagnose(req))

    assert result["engine"] == "llm:gpt-4o-mini"
    assert result["norm_references"] == [valid_ref]
    assert result["severity_level"] == "CRÍTICA"
    assert result["confidence_score"] < 100


def test_llm_failure_falls_back_to_local_composer(copilot, detector, monkeypatch):
    req = scenario_request(detector, "SWELL")
    real_client = httpx.AsyncClient
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        "backend.ai.copilot.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw),
    )
    result = asyncio.run(copilot.diagnose(req))
    assert result["engine"] == "local"
    assert result["primary_code"] == "SWELL"


def test_diagnosis_is_published_during_episode_and_cleared_after():
    from backend.main import app, sites

    st = sites["porto-nacional"]
    with TestClient(app) as client:
        client.headers.update(DEVICE_HEADERS)
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "senha-de-teste-123"}
        ).status_code == 200
        try:
            for tick in range(8):
                packet = generate_electrical_data("SAG_PALMAS", tick) | {"site_id": "porto-nacional"}
                client.post("/api/telemetry", json=packet)
            assert st.diagnosis["status"] == "ready"
            assert st.diagnosis["primary_code"] == "SAG"
            assert "Porto Nacional" in st.diagnosis["speech_summary"]
            snapshot = client.get("/api/state", params={"site": "porto-nacional"}).json()
            assert snapshot["diagnosis"]["id"] == st.diagnosis["id"]

            st.last_abnormal_at -= 60
            for tick in range(4):
                client.post("/api/telemetry", json=generate_electrical_data("NORMAL", tick) | {"site_id": "porto-nacional"})
            assert st.diagnosis is None
        finally:
            st.ats = TransferSwitch()


def test_diagnose_endpoint_requires_session_and_follows_schema():
    from backend.main import app

    with TestClient(app) as client:
        reading = generate_electrical_data("PHASE_LOSS") | {"site_id": "gurupi"}
        assert client.post("/api/v1/diagnose", json=reading).status_code == 401

        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "senha-de-teste-123"}
        ).status_code == 200
        body = client.post("/api/v1/diagnose", json=reading).json()
        assert body["primary_code"] == "PHASE_LOSS"
        assert {"cause_analysis", "severity_level", "confidence_score", "norm_references",
                "recommended_solutions", "speech_summary"} <= body.keys()
        assert "Gurupi" in body["speech_summary"]

        normal = client.post("/api/v1/diagnose", json=generate_electrical_data("NORMAL")).json()
        assert normal["status"] == "normal"
