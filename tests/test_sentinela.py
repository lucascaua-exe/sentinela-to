import pytest
from fastapi.testclient import TestClient

from backend.ai.anomaly_detector import AnomalyDetector
from backend.core.auth import LoginThrottle
from backend.core.transfer_switch import TransferSwitch
from simulator.iot_emulator import generate_electrical_data

DEVICE_HEADERS = {"X-Device-Token": "token-de-teste"}


@pytest.fixture(scope="module")
def detector():
    return AnomalyDetector()


def run_scenario(detector, mode, n=12):
    detector.reset_window()
    return [detector.analyze(generate_electrical_data(mode, tick=i)) for i in range(n)]


def device_client(app):
    client = TestClient(app)
    client.headers.update(DEVICE_HEADERS)
    return client


def test_normal_operation_has_no_false_alarms(detector):
    results = run_scenario(detector, "NORMAL", n=200)
    assert not any(r.severity in ("WARNING", "CRITICAL") for r in results)


@pytest.mark.parametrize(
    "mode, diagnosis",
    [
        ("NORMAL", "Rede operando em conformidade com os padrões da ANEEL."),
        ("SAG_PALMAS", "Subtensão severa identificada no QGD. Risco iminente de sobrecorrente nos compressores."),
        ("SWELL", "Sobretensão prejudicial isolada. Ativando desacoplamento de proteção."),
        ("PHASE_LOSS", "Desequilíbrio crítico trifásico."),
    ],
)
def test_demo_scenarios_diagnosis(detector, mode, diagnosis):
    assert run_scenario(detector, mode)[-1].diagnosis == diagnosis


def test_phase_loss_arms_transfer_in_3s():
    from backend.main import app, state

    with device_client(app) as client:
        for i in range(6):
            client.post("/api/telemetry", json=generate_electrical_data("PHASE_LOSS", tick=i))
        snap = state.ats.snapshot()
        assert snap["state"] == "ARMED"
        assert snap["countdown_total_s"] == 3.0
        state.ats.cancel()
        client.post("/api/telemetry", json=generate_electrical_data("NORMAL"))


@pytest.mark.parametrize("mode", ["SAG_PALMAS", "BLACKOUT", "SWELL", "PHASE_LOSS"])
def test_severe_disturbances_are_critical(detector, mode):
    results = run_scenario(detector, mode)
    assert results[-1].severity == "CRITICAL"


@pytest.mark.parametrize("mode", ["PHASE_IMBALANCE", "HARMONIC"])
def test_quality_disturbances_raise_warning_or_more(detector, mode):
    results = run_scenario(detector, mode)
    assert results[-1].severity in ("WARNING", "CRITICAL")


def test_sag_message_matches_operator_alert(detector):
    last = run_scenario(detector, "SAG_PALMAS")[-1]
    assert last.headline.startswith("ALERTA CRÍTICO: Queda de tensão nas Fases A e B")
    assert "sobrecorrente" in last.headline
    assert last.affected_phases == ["A", "B"]


def test_recovery_clears_alert_immediately(detector):
    run_scenario(detector, "SAG_PALMAS")
    first_normal = detector.analyze(generate_electrical_data("NORMAL"))
    assert first_normal.severity in ("NORMAL", "INFO")


def test_single_disturbance_right_after_start_is_not_critical(detector):
    detector.reset_window()
    first = detector.analyze(generate_electrical_data("SAG_PALMAS"))
    assert first.severity not in ("WARNING", "CRITICAL")


def test_pre_fault_degradation_is_flagged_by_ai(detector):
    results = run_scenario(detector, "PRE_FAULT", n=45)
    assert any(r.is_anomaly for r in results)


def test_detection_latency_is_milliseconds(detector):
    results = run_scenario(detector, "SAG_PALMAS", n=20)
    assert max(r.latency_ms for r in results) < 100


def test_ats_full_cycle():
    ats = TransferSwitch(countdown_s=5, restore_after_stable_s=10, sync_duration_s=3)
    t = 1000.0
    assert ats.observe("CRITICAL", "queda", now=t).to_state == "ARMED"
    assert ats.tick(now=t + 4.9) is None
    assert ats.tick(now=t + 5.0).to_state == "BACKUP"
    ats.observe("NORMAL", "", now=t + 6)
    assert ats.tick(now=t + 16).to_state == "RESTORING"
    assert ats.tick(now=t + 19).to_state == "GRID"
    assert ats.transfers_executed == 1


def test_ats_aborts_on_transient():
    ats = TransferSwitch(abort_after_normal_samples=4)
    t = 1000.0
    ats.observe("CRITICAL", "queda", now=t)
    events = [ats.observe("NORMAL", "", now=t + 0.5 * i) for i in range(1, 5)]
    assert events[-1].to_state == "GRID"
    assert ats.aborted_transients == 1
    assert ats.transfers_executed == 0


def test_operator_cancel_holds_until_grid_normalizes():
    ats = TransferSwitch()
    t = 1000.0
    ats.observe("CRITICAL", "queda", now=t)
    assert ats.cancel(now=t + 1).to_state == "GRID"
    assert ats.observe("CRITICAL", "queda", now=t + 1.5) is None
    ats.observe("NORMAL", "", now=t + 2)
    assert ats.observe("CRITICAL", "nova queda", now=t + 3).to_state == "ARMED"


def test_low_battery_sheds_non_critical_loads():
    ats = TransferSwitch(battery_soc=16.0)
    t = 1000.0
    ats.force_backup(now=t)
    load_before = ats.snapshot(now=t)["load_kw"]
    notice = None
    for i in range(1, 200):
        notice = ats.tick(now=t + i) or notice
        if ats.load_shed:
            break
    assert notice is not None and notice.from_state == notice.to_state == "BACKUP"
    assert ats.snapshot(now=t + i)["load_kw"] < load_before
    soc = ats.battery_soc
    ats.tick(now=t + i + 60)
    assert ats.battery_soc >= soc


def test_manual_transfer_is_not_counted_as_avoided_loss():
    ats = TransferSwitch()
    ats.force_backup(now=1000.0)
    assert ats.transfers_executed == 0
    assert ats.manual_transfers == 1


def login(client, username="admin", password="senha-de-teste-123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_api_ingestion_and_mode_command():
    from backend.main import app

    with device_client(app) as client:
        assert login(client).status_code == 200
        resp = client.post("/api/telemetry", json=generate_electrical_data("NORMAL"))
        assert resp.status_code == 200
        assert resp.json()["command_mode"] == "NORMAL"

        assert client.post("/api/simulator/mode", json={"mode": "BLACKOUT"}).status_code == 200
        assert client.post("/api/telemetry", json=generate_electrical_data("NORMAL")).json()["command_mode"] == "BLACKOUT"
        assert client.post("/api/simulator/mode", json={"mode": "INVALIDO"}).status_code == 400
        client.post("/api/simulator/mode", json={"mode": "NORMAL"})

        with client.websocket_connect("/ws/live") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert len(snapshot["history"]) >= 2


def test_telemetry_requires_device_token_and_valid_values():
    from backend.main import app

    with TestClient(app) as client:
        packet = generate_electrical_data("NORMAL")
        assert client.post("/api/telemetry", json=packet).status_code == 401
        assert client.post("/api/telemetry", json=packet, headers={"X-Device-Token": "errado"}).status_code == 401
        assert client.get("/api/telemetry/sites").status_code == 401

        invalid = packet | {"power_factor": 7}
        assert client.post("/api/telemetry", json=invalid, headers=DEVICE_HEADERS).status_code == 422
        sites = client.get("/api/telemetry/sites", headers=DEVICE_HEADERS).json()
        assert {"id": "gurupi", "device_id": "QGD-GURUPI-03"}.items() <= sites[2].items()


def test_telemetry_websocket_survives_bad_packets():
    from backend.main import app

    with device_client(app) as client:
        with client.websocket_connect("/ws/telemetry", headers=DEVICE_HEADERS) as ws:
            ws.send_json({"voltage_a": "abc"})
            assert ws.receive_json()["status"] == "error"
            ws.send_json(generate_electrical_data("NORMAL") | {"site_id": "inexistente"})
            assert ws.receive_json()["status"] == "error"
            ws.send_json(generate_electrical_data("NORMAL"))
            assert ws.receive_json()["status"] == "ok"


def test_sites_are_isolated():
    from backend.main import app, sites

    with device_client(app) as client:
        assert login(client).status_code == 200
        listed = client.get("/api/sites").json()
        assert [s["id"] for s in listed] == list(sites)

        assert client.post("/api/simulator/mode", json={"mode": "SWELL", "site": "gurupi"}).status_code == 200
        gurupi = generate_electrical_data("NORMAL") | {"site_id": "gurupi"}
        record = client.post("/api/telemetry", json=gurupi).json()
        assert record["command_mode"] == "SWELL"
        assert sites["gurupi"].history[-1]["device_id"] == "QGD-GURUPI-03"
        palmas = client.post("/api/telemetry", json=generate_electrical_data("NORMAL")).json()
        assert palmas["command_mode"] != "SWELL"

        assert client.get("/api/state", params={"site": "gurupi"}).json()["site"] == "gurupi"
        assert client.get("/api/state", params={"site": "inexistente"}).status_code == 404
        unknown = generate_electrical_data("NORMAL") | {"site_id": "inexistente"}
        assert client.post("/api/telemetry", json=unknown).status_code == 404

        with client.websocket_connect("/ws/live?site=gurupi") as ws:
            assert ws.receive_json()["site"] == "gurupi"
        client.post("/api/simulator/mode", json={"mode": "NORMAL", "site": "gurupi"})


def test_dashboard_requires_login():
    from starlette.websockets import WebSocketDisconnect

    from backend.main import app

    with device_client(app) as client:
        page = client.get("/", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login"
        assert client.get("/login").status_code == 200
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/simulator/mode", json={"mode": "NORMAL"}).status_code == 401
        assert client.post("/api/telemetry", json=generate_electrical_data("NORMAL")).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/live") as ws:
                ws.receive_json()


def test_live_feed_closes_when_session_is_revoked(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    import backend.main as main

    monkeypatch.setattr(main, "SESSION_RECHECK_S", 0.1)
    with TestClient(main.app) as client:
        assert login(client).status_code == 200
        with client.websocket_connect("/ws/live?site=araguaina") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            main.users.set_password("admin", "senha-de-teste-123")
            with pytest.raises(WebSocketDisconnect):
                for _ in range(50):
                    ws.receive_json()


def test_login_session_and_logout():
    from backend.main import app

    with TestClient(app) as client:
        assert login(client, password="errada").status_code == 401
        assert login(client, username="ninguem").status_code == 401

        resp = login(client)
        assert resp.status_code == 200
        cookie = resp.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.get("/api/auth/me").json() == {"username": "admin"}
        assert client.get("/", follow_redirects=False).status_code == 200
        assert client.get("/login", follow_redirects=False).status_code == 303

        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401


def test_login_is_throttled_after_repeated_failures():
    from backend.main import app, login_throttle

    with TestClient(app) as client:
        try:
            codes = [login(client, password=f"errada-{i}").status_code for i in range(6)]
            assert codes[:5] == [401] * 5
            assert codes[5] == 429
            assert login(client).status_code == 429
        finally:
            login_throttle.reset("testclient")


def test_throttle_reserves_attempt_before_password_check():
    throttle = LoginThrottle(max_failures=2)
    assert throttle.attempt("10.0.0.1") and throttle.attempt("10.0.0.1")
    assert not throttle.attempt("10.0.0.1")
    throttle.reset("10.0.0.1")
    assert throttle.attempt("10.0.0.1")


def test_passwords_are_stored_hashed():
    import sqlite3

    from backend.main import DB_PATH

    with sqlite3.connect(DB_PATH) as db:
        stored = db.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()[0]
    assert stored.startswith("pbkdf2_sha256$") and "senha-de-teste-123" not in stored


def test_flickering_anomaly_does_not_flood_alerts():
    from backend.main import app, state

    with device_client(app) as client:
        before = len(state.alerts)
        for i in range(80):
            client.post("/api/telemetry", json=generate_electrical_data("PRE_FAULT", tick=i))
        ia_alerts = [a for a in list(state.alerts)[: len(state.alerts) - before] if a["source"] == "IA"]
        assert 1 <= len(ia_alerts) <= 6
