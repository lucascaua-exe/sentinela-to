"""
Sentinela TO - Backend API (FastAPI)

Execução (a partir da raiz do projeto):
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.ai.anomaly_detector import SEVERITY_ORDER, AnomalyDetector, extract_features
from backend.ai.copilot import CODE_TERMS, CONFIDENCE_THRESHOLD, DiagnosisRequest, DiagnosticCopilot, ordered_codes
from backend.ai.knowledge_base import KnowledgeBase
from backend.ai.learning import GapNotFound, KnowledgeCuration
from backend.core.auth import (
    DB_PATH,
    DEVICE_TOKEN_HEADER,
    SESSION_COOKIE,
    SESSION_TTL_S,
    LoginThrottle,
    UserStore,
    device_token_valid,
    load_device_token,
)
from backend.core.event_log import EpisodeFilter, EventLog
from backend.core.settings import SettingsStore
from backend.core.transfer_switch import TransferEvent, TransferSwitch
from backend.reports.report import ReportMeta, build_csv, build_pdf, code_label, fmt_ts

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
DEVKIT_DIR = ROOT_DIR / "devkit"
# Rotas de API sem sessão de usuário: ingestão do medidor IoT (protegida por token do dispositivo),
# healthcheck e o próprio login.
PUBLIC_API = {"/api/telemetry", "/api/telemetry/sites", "/api/health", "/api/auth/login", "/api/auth/logout"}
DASHBOARD_PAGES = {"/", "/index.html"}
EPISODE_RESET_S = 5.0
ONLINE_TIMEOUT_S = 3.0
BROADCAST_TIMEOUT_S = 1.0
SESSION_RECHECK_S = 30.0
SIMULATOR_MODES = ("NORMAL", "PRE_FAULT", "SAG_PALMAS", "SWELL", "PHASE_LOSS", "PHASE_IMBALANCE", "HARMONIC", "BLACKOUT")
# Operação monofásica aquece os motores mais rápido: comutação antecipada.
PHASE_LOSS_COUNTDOWN_S = 3.0

SITES = {
    "palmas": {
        "city": "Palmas",
        "unit": "QGD 01",
        "location": "Distrito Industrial de Palmas - Setor 02",
        "device_id": "QGD-PALMAS-01",
    },
    "araguaina": {
        "city": "Araguaína",
        "unit": "QGD 02",
        "location": "Distrito Agroindustrial de Araguaína",
        "device_id": "QGD-ARAGUAINA-02",
    },
    "gurupi": {
        "city": "Gurupi",
        "unit": "QGD 03",
        "location": "Distrito Industrial de Gurupi",
        "device_id": "QGD-GURUPI-03",
    },
    "porto-nacional": {
        "city": "Porto Nacional",
        "unit": "QGD 04",
        "location": "Polo Industrial de Porto Nacional",
        "device_id": "QGD-PORTO-NACIONAL-04",
    },
}
DEFAULT_SITE = "palmas"

# Parâmetros econômicos de referência (ajustáveis por planta).
ECONOMICS = {
    "tariff_brl_mwh": 1155.20,
    "downtime_cost_brl_h": 35000.0,
    "avoided_downtime_h_per_event": 2.0,
    "motor_replacement_cost_brl": 18000.0,
}


class Telemetry(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    timestamp: float = Field(default_factory=time.time)
    voltage_a: float = Field(ge=0, le=1000)
    voltage_b: float = Field(ge=0, le=1000)
    voltage_c: float = Field(ge=0, le=1000)
    current_total: float = Field(ge=0, le=10000)
    frequency: float = Field(ge=0, le=100)
    power_factor: float = Field(ge=0, le=1)
    thd_voltage: float = Field(default=3.0, ge=0, le=100)
    location: str | None = None
    device_id: str | None = None
    site_id: str = DEFAULT_SITE


class ModeRequest(BaseModel):
    mode: str
    site: str = DEFAULT_SITE


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ExpertFeedback(BaseModel):
    gap_id: int
    correct_cause: str = Field(min_length=10, max_length=600)
    immediate_actions: list[str] = Field(min_length=1, max_length=4)
    preventive_actions: list[str] = Field(default_factory=list, max_length=4)
    norm_reference: str = Field(default="", max_length=160)

    @field_validator("correct_cause", "norm_reference")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("immediate_actions", "preventive_actions")
    @classmethod
    def _clean_actions(cls, actions: list[str]) -> list[str]:
        cleaned = [" ".join(a.split()) for a in actions if a.strip()]
        if any(len(a) > 300 for a in cleaned):
            raise ValueError("Cada ação deve ter até 300 caracteres.")
        return cleaned


class ConnectionHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        # Envio paralelo com prazo: um painel travado não pode segurar a telemetria nem a contagem da chave.
        clients = list(self.clients)
        if not clients:
            return
        results = await asyncio.gather(
            *(asyncio.wait_for(ws.send_json(message), BROADCAST_TIMEOUT_S) for ws in clients),
            return_exceptions=True,
        )
        for ws, result in zip(clients, results):
            if isinstance(result, BaseException):
                self.disconnect(ws)


class SentinelaState:
    def __init__(self, site_id: str = DEFAULT_SITE, model=None) -> None:
        self.site_id = site_id
        self.detector = AnomalyDetector(model=model)
        self.ats = TransferSwitch()
        self.hub = ConnectionHub()
        self.command_mode = "NORMAL"
        self.history: deque[dict] = deque(maxlen=240)
        self.alerts: deque[dict] = deque(maxlen=60)
        self.episode_severity = "NORMAL"
        self.episode_causes: set[tuple[str, str]] = set()
        self.last_abnormal_at = 0.0
        self.started_at = time.time()
        self.last_packet_at: float | None = None
        self.critical_events = 0
        self.normal_samples = 0
        self.latencies: deque[float] = deque(maxlen=200)
        self.diagnosis: dict | None = None
        self.diagnosis_task: asyncio.Task | None = None
        self.episode_id: int | None = None
        self.episode_extremes: dict = {}

    @property
    def online(self) -> bool:
        return bool(self.last_packet_at and time.time() - self.last_packet_at < ONLINE_TIMEOUT_S)

    def metrics(self) -> dict:
        # Só comutações automáticas contam como perda evitada; as manuais são testes ou manobras do operador.
        transfers = self.ats.transfers_executed
        avoided = transfers * (
            ECONOMICS["downtime_cost_brl_h"] * ECONOMICS["avoided_downtime_h_per_event"]
            + ECONOMICS["motor_replacement_cost_brl"]
        )
        avg_latency = sum(self.latencies) / len(self.latencies) if self.latencies else 0.0
        samples = self.detector.samples_processed
        return {
            "samples_processed": samples,
            "stability_pct": round(100.0 * self.normal_samples / samples, 1) if samples else 100.0,
            "critical_events": self.critical_events,
            "transfers_executed": transfers,
            "manual_transfers": self.ats.manual_transfers,
            "aborted_transients": self.ats.aborted_transients,
            "avoided_loss_brl": round(avoided, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "uptime_s": round(time.time() - self.started_at),
            "simulator_online": self.online,
            "economics": ECONOMICS,
        }

    def summary(self) -> dict:
        last = self.history[-1]["analysis"]["severity"] if self.history else None
        online = self.online
        return {
            "id": self.site_id,
            **SITES[self.site_id],
            "online": online,
            "severity": last if online else None,
            "ats_state": self.ats.state,
        }

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "site": self.site_id,
            "history": list(self.history),
            "alerts": list(self.alerts),
            "ats": self.ats.snapshot(),
            "metrics": self.metrics(),
            "command_mode": self.command_mode,
            "modes": SIMULATOR_MODES,
            "diagnosis": self.diagnosis,
        }

    def push_alert(self, level: str, title: str, detail: str, source: str) -> dict:
        alert = {
            "id": uuid.uuid4().hex[:10],
            "timestamp": time.time(),
            "level": level,
            "title": title,
            "detail": detail,
            "source": source,
        }
        self.alerts.appendleft(alert)
        event_log.record_event(self.site_id, self.episode_id, alert["timestamp"], level, source, title, detail)
        return alert

    def track_extremes(self, sample: dict) -> None:
        ext = self.episode_extremes
        voltages = (sample["voltage_a"], sample["voltage_b"], sample["voltage_c"])
        for key, value, pick in (
            ("min_voltage", min(voltages), min),
            ("max_voltage", max(voltages), max),
            ("max_current", sample["current_total"], max),
            ("min_power_factor", sample["power_factor"], min),
            ("max_thd", sample.get("thd_voltage", 0.0), max),
        ):
            ext[key] = round(pick(ext.get(key, value), value), 2)


_shared_model = AnomalyDetector().model
sites = {site_id: SentinelaState(site_id, _shared_model) for site_id in SITES}
state = sites[DEFAULT_SITE]
users = UserStore(DB_PATH)
login_throttle = LoginThrottle()
DEVICE_TOKEN = load_device_token()
curation = KnowledgeCuration(DB_PATH)
event_log = EventLog(DB_PATH)
settings_store = SettingsStore(DB_PATH)
REPORT_MAX_EPISODES = 500
copilot = DiagnosticCopilot(KnowledgeBase(extra=curation.validated_chunks(CODE_TERMS)))


def get_site(site_id: str) -> SentinelaState:
    if site_id not in sites:
        raise HTTPException(status_code=404, detail=f"Unidade desconhecida. Opções: {', '.join(sites)}")
    return sites[site_id]


def require_device(request: Request) -> None:
    if not device_token_valid(request.headers.get(DEVICE_TOKEN_HEADER), DEVICE_TOKEN):
        raise HTTPException(status_code=401, detail="Token do dispositivo ausente ou inválido.")


def ensure_admin_user() -> None:
    """Na primeira execução, cria o administrador (senha via SENTINELA_ADMIN_PASSWORD ou gerada)."""
    if users.user_count():
        return
    username = os.environ.get("SENTINELA_ADMIN_USER", "admin")
    password = os.environ.get("SENTINELA_ADMIN_PASSWORD")
    if password is None:
        password = secrets.token_urlsafe(9)
        print(
            f"[Sentinela TO] Primeiro acesso: usuário '{username}' / senha '{password}'. "
            f"Troque com: python -m scripts.create_user {username}",
            flush=True,
        )
    users.set_password(username, password)


ATS_TITLES = {
    "ARMED": "Chave de Transferência Automática armada: disparo em {countdown:.0f}s",
    "BACKUP": "Comutação executada: planta em autoprodução (Solar + BESS)",
    "RESTORING": "Sincronizando retorno à rede da concessionária",
    "GRID": "Planta operando na rede da concessionária",
}


async def publish_ats_event(st: SentinelaState, event: TransferEvent | None) -> None:
    if event is None:
        return
    if event.from_state == event.to_state:
        # Aviso operacional sem troca de estado (ex.: corte de cargas por bateria baixa).
        level, title = "CRITICAL", event.reason
    else:
        level = "CRITICAL" if event.to_state in ("ARMED", "BACKUP") else "INFO"
        title = ATS_TITLES[event.to_state].format(countdown=st.ats.active_countdown_s)
    alert = st.push_alert(level, title, event.reason, "ATS")
    if event.to_state == "BACKUP" and event.from_state != "BACKUP" and st.episode_id:
        event_log.mark_transferred(st.episode_id)
    await st.hub.broadcast({"type": "alert", "alert": alert})
    await st.hub.broadcast({"type": "ats", "ats": st.ats.snapshot(), "metrics": st.metrics()})


def diagnosis_request(site_id: str, sample: dict, severity: str, diagnostics: list[dict], risk: float) -> DiagnosisRequest:
    return DiagnosisRequest(
        site_id=site_id,
        city=SITES[site_id]["city"],
        location=sample.get("location") or SITES[site_id]["location"],
        sample=sample,
        severity=severity,
        codes=ordered_codes(diagnostics, severity),
        risk_score=risk,
    )


async def publish_diagnosis(st: SentinelaState, diagnosis: dict | None) -> None:
    st.diagnosis = diagnosis
    if diagnosis and diagnosis.get("status") == "ready" and st.episode_id:
        event_log.attach_diagnosis(st.episode_id, diagnosis)
    await st.hub.broadcast({"type": "diagnosis", "diagnosis": diagnosis})


async def autotrain(diagnosis: dict, req: DiagnosisRequest) -> dict:
    """Confiança abaixo do limiar: registra a lacuna na curadoria e procura documentos novos no repositório da base."""
    if not diagnosis.get("low_confidence"):
        return diagnosis
    gap = await asyncio.to_thread(curation.record_gap, diagnosis, req.codes, req.sample, req.city)
    refreshed = await asyncio.to_thread(copilot.kb.refresh_if_changed)
    if refreshed:
        diagnosis = await copilot.diagnose(req)
    diagnosis["autotrain"] = {
        "triggered": True,
        "gap_id": gap["id"],
        "occurrences": gap["occurrences"],
        "repository_refreshed": refreshed,
        "kb_version": copilot.kb.version,
    }
    return diagnosis


async def request_diagnosis(st: SentinelaState, record: dict) -> None:
    analysis = record["analysis"]
    req = diagnosis_request(st.site_id, record, analysis["severity"], analysis["diagnostics"], analysis["risk_score"])
    if st.diagnosis_task:
        st.diagnosis_task.cancel()
    if copilot.llm_model is None:
        await publish_diagnosis(st, await autotrain(copilot.diagnose_local(req), req))
        return
    # O LLM leva segundos: o painel mostra "analisando" e a telemetria segue sem esperar.
    await publish_diagnosis(st, {"status": "pending", "site": st.site_id, "severity_level": copilot.severity_level(req)})

    async def finish() -> None:
        await publish_diagnosis(st, await autotrain(await copilot.diagnose(req), req))

    st.diagnosis_task = asyncio.create_task(finish())


async def clear_diagnosis(st: SentinelaState) -> None:
    if st.diagnosis_task:
        st.diagnosis_task.cancel()
        st.diagnosis_task = None
    if st.diagnosis is not None:
        await publish_diagnosis(st, None)


async def ats_ticker() -> None:
    while True:
        await asyncio.sleep(0.25)
        for st in sites.values():
            event = st.ats.tick()
            if event:
                await publish_ats_event(st, event)
            elif st.ats.state in ("ARMED", "BACKUP", "RESTORING"):
                await st.hub.broadcast({"type": "ats", "ats": st.ats.snapshot(), "metrics": st.metrics()})


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_admin_user()
    event_log.close_dangling()
    task = asyncio.create_task(ats_ticker())
    yield
    task.cancel()


app = FastAPI(
    title="Sentinela TO API",
    description="Telemetria IoT e Inteligência Preditiva para Proteção Energética da Indústria Tocantinense",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    guarded_api = path.startswith("/api") and path not in PUBLIC_API
    if guarded_api or path in DASHBOARD_PAGES or path == "/login":
        user = users.session_user(request.cookies.get(SESSION_COOKIE))
        if user is None and guarded_api:
            return JSONResponse({"detail": "Sessão expirada ou inexistente."}, status_code=401)
        if user is None and path in DASHBOARD_PAGES:
            return RedirectResponse("/login", status_code=303)
        if user is not None and path == "/login":
            return RedirectResponse("/", status_code=303)
        request.state.user = user
    return await call_next(request)


@app.get("/login", include_in_schema=False)
async def login_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "login.html")


@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request, response: Response) -> dict:
    client = request.client.host if request.client else "desconhecido"
    if not login_throttle.attempt(client):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos e tente novamente.")
    user = await asyncio.to_thread(users.authenticate, req.username.strip(), req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
    login_throttle.reset(client)
    response.set_cookie(
        SESSION_COOKIE,
        users.create_session(user["id"]),
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"username": user["username"]}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    users.delete_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/auth/me")
async def me(request: Request) -> dict:
    return {"username": request.state.user}


async def process_telemetry(packet: Telemetry) -> dict:
    st = get_site(packet.site_id)
    sample = packet.model_dump()
    sample["location"] = sample["location"] or SITES[st.site_id]["location"]
    sample["device_id"] = sample["device_id"] or SITES[st.site_id]["device_id"]
    analysis = st.detector.analyze(sample)
    st.latencies.append(analysis.latency_ms)
    st.last_packet_at = time.time()
    if analysis.severity in ("NORMAL", "INFO"):
        st.normal_samples += 1

    record = {**sample, "analysis": analysis.as_dict()}
    st.history.append(record)

    previous = st.history[-2]["analysis"]["severity"] if len(st.history) > 1 else "NORMAL"
    if analysis.severity == "CRITICAL" and previous != "CRITICAL":
        st.critical_events += 1

    phase_lost = any(d.code == "PHASE_LOSS" for d in analysis.diagnostics)
    countdown = PHASE_LOSS_COUNTDOWN_S if phase_lost else st.ats.countdown_s

    level = SEVERITY_ORDER[analysis.severity]
    if level >= SEVERITY_ORDER["WARNING"]:
        cause = next((d.code for d in analysis.diagnostics if SEVERITY_ORDER[d.level] >= level), "AI_PATTERN")
        escalated = level > SEVERITY_ORDER[st.episode_severity]
        new_cause = (analysis.severity, cause) not in st.episode_causes
        if escalated or new_cause:
            title = analysis.headline
            if analysis.severity == "CRITICAL" and st.ats.can_arm:
                title += f" Disparando Chave de Transferência Automática em {countdown:.0f}s."
            if st.episode_id is None:
                st.episode_id = event_log.start_episode(st.site_id, time.time(), analysis.severity, cause, analysis.headline)
                st.episode_extremes = {}
            else:
                event_log.escalate(st.episode_id, analysis.severity, cause, analysis.headline)
            alert = st.push_alert(analysis.severity, title, analysis.recommended_action, "IA")
            st.episode_severity = max(st.episode_severity, analysis.severity, key=SEVERITY_ORDER.get)
            st.episode_causes.add((analysis.severity, cause))
            await st.hub.broadcast({"type": "alert", "alert": alert})
            await request_diagnosis(st, record)
        st.last_abnormal_at = time.time()
    elif time.time() - st.last_abnormal_at >= EPISODE_RESET_S:
        st.episode_severity = "NORMAL"
        st.episode_causes.clear()
        await clear_diagnosis(st)
        # A ocorrência só fecha com a planta de volta à rede: o retorno da chave entra na mesma linha do tempo.
        if st.episode_id and st.ats.state == "GRID":
            event_log.end_episode(st.episode_id, st.last_abnormal_at, st.episode_extremes)
            st.episode_id = None
    if st.episode_id:
        st.track_extremes(sample)

    await st.hub.broadcast({"type": "telemetry", "record": record, "metrics": st.metrics()})
    await publish_ats_event(st, st.ats.observe(analysis.severity, analysis.headline, countdown_s=countdown))

    return {
        "status": "ok",
        "severity": analysis.severity,
        "risk_score": analysis.risk_score,
        "latency_ms": analysis.latency_ms,
        "ats_state": st.ats.state,
        "command_mode": st.command_mode,
    }


@app.post("/api/telemetry", dependencies=[Depends(require_device)])
async def ingest_telemetry(packet: Telemetry) -> dict:
    return await process_telemetry(packet)


@app.get("/api/telemetry/sites", dependencies=[Depends(require_device)])
async def device_sites() -> list[dict]:
    """Cadastro de unidades para os medidores/simulador (fonte única: SITES)."""
    return [{"id": site_id, **meta} for site_id, meta in SITES.items()]


@app.websocket("/ws/telemetry")
async def ingest_telemetry_ws(ws: WebSocket) -> None:
    if not device_token_valid(ws.headers.get(DEVICE_TOKEN_HEADER), DEVICE_TOKEN):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            try:
                reply = await process_telemetry(Telemetry.model_validate(await ws.receive_json()))
            except ValidationError as exc:
                reply = {"status": "error", "detail": exc.errors(include_url=False, include_input=False, include_context=False)}
            except HTTPException as exc:
                reply = {"status": "error", "detail": exc.detail}
            except ValueError:
                reply = {"status": "error", "detail": "JSON inválido."}
            await ws.send_json(reply)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/live")
async def live_feed(ws: WebSocket) -> None:
    site_id = ws.query_params.get("site", DEFAULT_SITE)
    token = ws.cookies.get(SESSION_COOKIE)
    if users.session_user(token) is None or site_id not in sites:
        await ws.close(code=1008)
        return
    st = sites[site_id]
    await st.hub.connect(ws)
    try:
        await ws.send_json(st.snapshot())
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=SESSION_RECHECK_S)
            except asyncio.TimeoutError:
                # Sessão revogada (logout, troca de senha) ou expirada encerra o painel já aberto.
                if users.session_user(token) is None:
                    await ws.close(code=1008)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        st.hub.disconnect(ws)


@app.get("/api/health")
async def health() -> dict:
    online = {site_id: st.online for site_id, st in sites.items()}
    return {
        "status": "ok",
        "service": "sentinela-to",
        "simulator_online": any(online.values()),
        "sites": online,
        "copilot": {
            "engine": copilot.engine,
            "chunks": len(copilot.kb.chunks),
            "sources": len(copilot.kb.sources),
            "kb_version": copilot.kb.version,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
        },
    }


@app.post("/api/v1/diagnose")
async def diagnose(packet: Telemetry) -> dict:
    """Diagnóstico RAG sob demanda para uma leitura avulsa (classificação só pelas regras, sem a janela da IA)."""
    get_site(packet.site_id)
    sample = packet.model_dump()
    diagnostics, _ = AnomalyDetector._diagnose(sample, extract_features(sample))
    levels = [SEVERITY_ORDER[d.level] for d in diagnostics]
    severity = next(name for name, lvl in SEVERITY_ORDER.items() if lvl == max(levels, default=0))
    if SEVERITY_ORDER[severity] < SEVERITY_ORDER["WARNING"]:
        return {"status": "normal", "site": packet.site_id, "detail": "Leitura dentro das faixas adequadas; nenhuma anomalia a diagnosticar."}
    req = diagnosis_request(packet.site_id, sample, severity, [d.as_dict() for d in diagnostics], 0.0)
    return await autotrain(await copilot.diagnose(req), req)


@app.post("/api/v1/feedback")
async def expert_feedback(feedback: ExpertFeedback, request: Request) -> dict:
    """Resolução validada pela engenharia: vira trecho da base e o índice é reconstruído sem reiniciar o backend."""
    try:
        resolution_id = await asyncio.to_thread(
            curation.resolve,
            feedback.gap_id,
            feedback.correct_cause,
            feedback.immediate_actions,
            feedback.preventive_actions,
            feedback.norm_reference,
            request.state.user,
        )
    except GapNotFound:
        raise HTTPException(status_code=404, detail="Lacuna não encontrada.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    version = await asyncio.to_thread(copilot.kb.reindex, curation.validated_chunks(CODE_TERMS))

    # Unidades ainda exibindo o diagnóstico incerto desta lacuna recebem o diagnóstico retreinado na hora.
    for st in sites.values():
        current = st.diagnosis or {}
        if (current.get("autotrain") or {}).get("gap_id") == feedback.gap_id and st.history:
            await request_diagnosis(st, st.history[-1])

    return {
        "status": "SUCCESS",
        "resolution_id": resolution_id,
        "kb_version": version,
        "chunks": len(copilot.kb.chunks),
        "message": f"Lacuna #{feedback.gap_id} validada e indexada na base RAG.",
    }


@app.get("/api/v1/knowledge/gaps")
async def knowledge_gaps(status: str = "OPEN") -> dict:
    status = status.upper()
    if status not in {"OPEN", "RESOLVED", "DISMISSED", "ALL"}:
        raise HTTPException(status_code=422, detail="Status inválido.")
    gaps = await asyncio.to_thread(curation.list_gaps, None if status == "ALL" else status)
    return {
        "gaps": gaps,
        "counts": await asyncio.to_thread(curation.counts),
        "kb": {"version": copilot.kb.version, "chunks": len(copilot.kb.chunks), "threshold": CONFIDENCE_THRESHOLD},
    }


@app.post("/api/v1/knowledge/gaps/{gap_id}/dismiss")
async def dismiss_gap(gap_id: int, request: Request) -> dict:
    try:
        await asyncio.to_thread(curation.dismiss, gap_id, request.state.user)
    except GapNotFound:
        raise HTTPException(status_code=404, detail="Lacuna não encontrada.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"status": "ok"}


@app.post("/api/v1/knowledge/reindex")
async def reindex_knowledge() -> dict:
    """Reindexa documentos do repositório e resoluções validadas, sem interromper o atendimento."""
    version = await asyncio.to_thread(copilot.kb.reindex, curation.validated_chunks(CODE_TERMS))
    return {"kb_version": version, "chunks": len(copilot.kb.chunks), "sources": len(copilot.kb.sources)}


@app.get("/api/v1/settings")
async def get_settings(request: Request) -> dict:
    return (await asyncio.to_thread(settings_store.get, request.state.user)).model_dump()


@app.put("/api/v1/settings")
async def update_settings(changes: dict, request: Request) -> dict:
    if changes.get("default_site") not in (None, "", *SITES):
        raise HTTPException(status_code=422, detail="Unidade padrão desconhecida.")
    try:
        saved = await asyncio.to_thread(settings_store.update, request.state.user, changes)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_url=False, include_input=False, include_context=False)
        ) from None
    return saved.model_dump()


def _csv_param(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def episode_filter(
    start: float | None = None,
    end: float | None = None,
    sites: str | None = None,
    severities: str | None = None,
    codes: str | None = None,
    transferred: bool | None = None,
    q: str = "",
) -> EpisodeFilter:
    """Filtros comuns ao histórico e aos relatórios (start/end em segundos Unix; listas separadas por vírgula)."""
    return EpisodeFilter(
        start=start,
        end=end,
        sites=[s for s in _csv_param(sites) if s in SITES],
        severities=[s.upper() for s in _csv_param(severities) if s.upper() in ("CRITICAL", "WARNING")],
        codes=[c.upper() for c in _csv_param(codes)],
        transferred=transferred,
        query=q.strip()[:80],
    )


def _with_live_extremes(episodes: list[dict]) -> list[dict]:
    """Ocorrências em curso ainda não gravaram os extremos: completa com os valores acumulados em memória."""
    for ep in episodes:
        st = sites.get(ep["site_id"])
        if ep["ongoing"] and st and st.episode_id == ep["id"]:
            for key, value in st.episode_extremes.items():
                ep[key] = value
    return episodes


@app.get("/api/v1/history/episodes")
async def history_episodes(
    flt: EpisodeFilter = Depends(episode_filter), limit: int = 100, offset: int = 0
) -> dict:
    limit = max(1, min(limit, 500))
    episodes, total = await asyncio.to_thread(event_log.episodes, flt, limit, max(0, offset))
    stats = await asyncio.to_thread(event_log.stats, flt)
    return {"episodes": _with_live_extremes(episodes), "total": total, "stats": stats}


@app.get("/api/v1/history/episodes/{episode_id}")
async def history_episode(episode_id: int) -> dict:
    ep = await asyncio.to_thread(event_log.episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Ocorrência não encontrada.")
    return _with_live_extremes([ep])[0]


def _report_meta(flt: EpisodeFilter, user: str) -> ReportMeta:
    prefs = settings_store.get(user)
    if flt.start or flt.end:
        start = fmt_ts(flt.start).split(" ")[0] if flt.start else "início dos registros"
        end = fmt_ts(flt.end).split(" ")[0] if flt.end else fmt_ts(time.time()).split(" ")[0]
        period = f"De {start} a {end}"
    else:
        period = "Todo o histórico registrado"
    names = {site_id: f"{meta['city']} · {meta['unit']}" for site_id, meta in SITES.items()}
    filters = []
    if flt.severities:
        filters.append("severidade " + " e ".join("crítica" if s == "CRITICAL" else "atenção" for s in flt.severities))
    if flt.codes:
        filters.append("distúrbio " + ", ".join(code_label(c).lower() for c in flt.codes))
    if flt.transferred is not None:
        filters.append("com comutação" if flt.transferred else "sem comutação")
    if flt.query:
        filters.append(f'busca "{flt.query}"')
    return ReportMeta(
        period=period,
        sites=", ".join(names[s] for s in flt.sites) if flt.sites else "Todas as unidades",
        filters="; ".join(filters) if filters else "Nenhum",
        generated_by=user,
        company=prefs.report_company,
        responsible=prefs.report_responsible,
        site_names=names,
        loss_per_transfer_brl=ECONOMICS["downtime_cost_brl_h"] * ECONOMICS["avoided_downtime_h_per_event"]
        + ECONOMICS["motor_replacement_cost_brl"],
    )


@app.get("/api/v1/reports/occurrences.{fmt}")
async def occurrences_report(fmt: str, request: Request, flt: EpisodeFilter = Depends(episode_filter)) -> Response:
    if fmt not in ("pdf", "csv"):
        raise HTTPException(status_code=404, detail="Formato disponível: pdf ou csv.")
    episodes, _ = await asyncio.to_thread(event_log.episodes, flt, REPORT_MAX_EPISODES, 0)
    episodes = _with_live_extremes(episodes)
    meta = await asyncio.to_thread(_report_meta, flt, request.state.user)
    if fmt == "pdf":
        stats = await asyncio.to_thread(event_log.stats, flt)
        content = await asyncio.to_thread(build_pdf, episodes, stats, meta)
        media = "application/pdf"
    else:
        content = await asyncio.to_thread(build_csv, episodes, meta)
        media = "text/csv; charset=utf-8"
    filename = time.strftime(f"sentinela-to-ocorrencias-%Y%m%d-%H%M.{fmt}")
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/sites")
async def list_sites() -> list[dict]:
    return [st.summary() for st in sites.values()]


@app.get("/api/state")
async def get_state(site: str = DEFAULT_SITE) -> dict:
    return get_site(site).snapshot()


@app.post("/api/simulator/mode")
async def set_simulator_mode(req: ModeRequest) -> dict:
    st = get_site(req.site)
    mode = req.mode.upper()
    if mode not in SIMULATOR_MODES:
        raise HTTPException(status_code=400, detail=f"Modo inválido. Opções: {', '.join(SIMULATOR_MODES)}")
    st.command_mode = mode
    await st.hub.broadcast({"type": "mode", "command_mode": mode})
    return {"site": st.site_id, "command_mode": mode}


@app.post("/api/ats/cancel")
async def ats_cancel(site: str = DEFAULT_SITE) -> dict:
    st = get_site(site)
    await publish_ats_event(st, st.ats.cancel())
    return st.ats.snapshot()


@app.post("/api/ats/force")
async def ats_force(site: str = DEFAULT_SITE) -> dict:
    st = get_site(site)
    await publish_ats_event(st, st.ats.force_backup())
    return st.ats.snapshot()


@app.post("/api/ats/restore")
async def ats_restore(site: str = DEFAULT_SITE) -> dict:
    st = get_site(site)
    await publish_ats_event(st, st.ats.force_restore())
    return st.ats.snapshot()


if DEVKIT_DIR.exists():
    app.mount("/devkit", StaticFiles(directory=DEVKIT_DIR), name="devkit")

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
