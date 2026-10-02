import csv
import io
import time

import pytest
from fastapi.testclient import TestClient

from backend.core.event_log import EpisodeFilter, EventLog
from backend.core.settings import SettingsStore
from backend.reports.report import ReportMeta, build_csv, build_pdf
from simulator.iot_emulator import generate_electrical_data

DEVICE_HEADERS = {"X-Device-Token": "token-de-teste"}
DIAGNOSIS = {
    "hypothesis": "",
    "cause_analysis": "Afundamento de tensão provocado pela partida do compressor.",
    "confidence_score": 96.4,
    "low_confidence": False,
    "measurement": "VA 172 V; VB 175 V",
    "recommended_solutions": [
        {"type": "IMEDIATA", "action": "Isolar as cargas indutivas.", "contingency": False},
        {"type": "IMEDIATA", "action": "Acione a engenharia.", "contingency": True},
        {"type": "PREVENTIVA", "action": "Instalar soft-starter.", "contingency": False},
    ],
    "norm_references": ["ANEEL PRODIST Módulo 8 — Seção 8.1"],
}


def login(client):
    return client.post("/api/auth/login", json={"username": "admin", "password": "senha-de-teste-123"})


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path / "eventos.db")


def make_episode(log, site, started, severity="CRITICAL", code="SAG", transferred=False, duration=12.0):
    ep_id = log.start_episode(site, started, severity, code, f"{code} em {site}")
    log.record_event(site, ep_id, started, severity, "IA", "Alerta", "detalhe")
    log.attach_diagnosis(ep_id, DIAGNOSIS)
    if transferred:
        log.mark_transferred(ep_id)
    log.end_episode(ep_id, started + duration, {"min_voltage": 171.2, "max_voltage": 221.0, "max_current": 48.0})
    return ep_id


def test_episode_lifecycle_keeps_peak_codes_diagnosis_and_events(log):
    t = time.time() - 60
    ep_id = log.start_episode("palmas", t, "WARNING", "IMBALANCE", "Desequilíbrio")
    log.escalate(ep_id, "CRITICAL", "SAG", "Afundamento")
    log.escalate(ep_id, "WARNING", "HARMONIC", "Harmônicos")
    log.record_event("palmas", ep_id, t + 1, "CRITICAL", "IA", "Afundamento", "Fases A e B")
    log.attach_diagnosis(ep_id, DIAGNOSIS)
    log.mark_transferred(ep_id)

    ongoing = log.episode(ep_id)
    assert ongoing["ongoing"] and ongoing["peak_severity"] == "CRITICAL"
    assert ongoing["primary_code"] == "SAG" and ongoing["codes"] == ["IMBALANCE", "SAG", "HARMONIC"]
    assert ongoing["actions"] == [
        {"type": "IMEDIATA", "action": "Isolar as cargas indutivas."},
        {"type": "PREVENTIVA", "action": "Instalar soft-starter."},
    ]

    log.end_episode(ep_id, t + 30, {"min_voltage": 170.0})
    done = log.episode(ep_id)
    assert not done["ongoing"] and done["duration_s"] == 30.0 and done["transferred"]
    assert done["min_voltage"] == 170.0
    assert [e["title"] for e in done["events"]] == ["Afundamento"]


def test_dangling_episodes_are_closed_at_last_event(log):
    t = time.time() - 100
    ep_id = log.start_episode("gurupi", t, "CRITICAL", "SAG", "Afundamento")
    log.record_event("gurupi", ep_id, t + 7, "CRITICAL", "ATS", "Chave armada")
    assert log.close_dangling() == 1
    assert log.episode(ep_id)["ended_at"] == t + 7


def test_filters_and_stats(log):
    now = time.time()
    make_episode(log, "palmas", now - 3 * 86400, code="SAG", transferred=True)
    make_episode(log, "gurupi", now - 3600, severity="WARNING", code="HARMONIC")
    make_episode(log, "palmas", now - 600, code="PHASE_LOSS")

    _, total = log.episodes(EpisodeFilter())
    assert total == 3
    assert log.episodes(EpisodeFilter(start=now - 86400))[1] == 2
    assert log.episodes(EpisodeFilter(sites=["palmas"]))[1] == 2
    assert log.episodes(EpisodeFilter(severities=["WARNING"]))[1] == 1
    assert log.episodes(EpisodeFilter(codes=["PHASE_LOSS", "HARMONIC"]))[1] == 2
    assert log.episodes(EpisodeFilter(transferred=True))[1] == 1
    assert log.episodes(EpisodeFilter(query="compressor"))[1] == 3

    stats = log.stats(EpisodeFilter())
    assert (stats["total"], stats["critical"], stats["warning"], stats["transfers"]) == (3, 2, 1, 1)
    assert stats["by_site"] == {"palmas": 2, "gurupi": 1}
    assert sum(d["CRITICAL"] + d["WARNING"] for d in stats["by_day"].values()) == 3
    assert stats["total_duration_s"] == 36.0


def test_settings_merge_and_validate(tmp_path):
    store = SettingsStore(tmp_path / "prefs.db")
    assert store.get("ana").auto_voice is False
    store.update("ana", {"auto_voice": True, "report_company": "  Indústria   Exemplo "})
    saved = store.update("ANA", {"voice_rate": 1.2})
    assert saved.auto_voice and saved.voice_rate == 1.2 and saved.report_company == "Indústria Exemplo"
    with pytest.raises(ValueError):
        store.update("ana", {"voice_rate": 3})


def test_pdf_and_csv_builders(log):
    now = time.time()
    for i in range(30):
        make_episode(log, ["palmas", "gurupi"][i % 2], now - i * 7200, transferred=i % 3 == 0)
    episodes, _ = log.episodes(EpisodeFilter(), 500)
    meta = ReportMeta(
        period="Últimos 7 dias",
        sites="Todas as unidades",
        filters="Nenhum",
        generated_by="admin",
        company="Indústria Exemplo",
        responsible="Eng. Maria",
        site_names={"palmas": "Palmas · QGD 01", "gurupi": "Gurupi · QGD 03"},
        loss_per_transfer_brl=12000.0,
    )

    pdf = build_pdf(episodes, log.stats(EpisodeFilter()), meta)
    assert pdf.startswith(b"%PDF") and len(pdf) > 10_000

    rows = list(csv.reader(io.StringIO(build_csv(episodes, meta).decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 31
    assert rows[1][1] in ("Palmas · QGD 01", "Gurupi · QGD 03")


def test_settings_api_requires_login_and_validates():
    from backend.main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/settings").status_code == 401
        assert login(client).status_code == 200

        assert client.get("/api/v1/settings").json()["auto_voice"] is False
        resp = client.put("/api/v1/settings", json={"auto_voice": True, "default_site": "gurupi"})
        assert resp.status_code == 200 and resp.json()["auto_voice"] is True
        assert client.get("/api/v1/settings").json()["default_site"] == "gurupi"

        assert client.put("/api/v1/settings", json={"default_site": "marte"}).status_code == 422
        assert client.put("/api/v1/settings", json={"voice_volume": 2}).status_code == 422
        client.put("/api/v1/settings", json={"auto_voice": False, "default_site": ""})


def test_critical_telemetry_opens_episode_visible_in_history_and_reports():
    from backend.main import app, state

    with TestClient(app) as client:
        assert client.get("/api/v1/history/episodes").status_code == 401
        assert login(client).status_code == 200
        for i in range(6):
            client.post("/api/telemetry", json=generate_electrical_data("PHASE_LOSS", tick=i), headers=DEVICE_HEADERS)
        state.ats.cancel()

        body = client.get("/api/v1/history/episodes", params={"sites": state.site_id, "severities": "CRITICAL"}).json()
        assert body["total"] >= 1 and body["stats"]["critical"] >= 1
        latest = body["episodes"][0]
        assert latest["ongoing"] and latest["site_id"] == state.site_id
        assert latest["min_voltage"] is not None

        detail = client.get(f"/api/v1/history/episodes/{latest['id']}").json()
        assert any(e["source"] == "IA" and e["level"] == "CRITICAL" for e in detail["events"])
        assert client.get("/api/v1/history/episodes/999999").status_code == 404

        pdf = client.get("/api/v1/reports/occurrences.pdf", params={"start": int(time.time()) - 3600})
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert pdf.headers["content-disposition"].startswith('attachment; filename="sentinela-to-ocorrencias-')
        csv_resp = client.get("/api/v1/reports/occurrences.csv")
        assert csv_resp.status_code == 200 and csv_resp.content.startswith(b"\xef\xbb\xbf")
        assert client.get("/api/v1/reports/occurrences.xlsx").status_code == 404

        client.post("/api/telemetry", json=generate_electrical_data("NORMAL"), headers=DEVICE_HEADERS)
