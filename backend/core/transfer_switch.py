"""
Sentinela TO - Chave de Transferência Automática (ATS) virtual

Máquina de estados que representa o protocolo de isolamento da planta:

    GRID ──(anomalia crítica)──► ARMED ──(contagem expira)──► BACKUP
     ▲                            │                            │
     │                     (rede normaliza /                (rede estável
     │                      operador cancela)                por N segundos)
     │                            ▼                            ▼
     └────────────────────────── GRID ◄──(sincronismo)──── RESTORING

Em produção, as transições acionam um CLP/relé via Modbus TCP ou MQTT. No MVP
as transições são emuladas e transmitidas em tempo real ao dashboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

STATE_LABELS = {
    "GRID": "Rede da concessionária",
    "ARMED": "Comutação armada",
    "BACKUP": "Autoprodução (Solar + BESS)",
    "RESTORING": "Sincronizando retorno à rede",
}


@dataclass
class TransferEvent:
    timestamp: float
    from_state: str
    to_state: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
        }


@dataclass
class TransferSwitch:
    countdown_s: float = 5.0
    abort_after_normal_samples: int = 4
    restore_after_stable_s: float = 10.0
    sync_duration_s: float = 3.0
    battery_capacity_kwh: float = 500.0
    solar_peak_kw: float = 150.0
    plant_load_kw: float = 220.0
    low_battery_pct: float = 15.0

    state: str = "GRID"
    battery_soc: float = 92.0
    armed_at: float | None = None
    active_countdown_s: float = 5.0
    state_since: float = field(default_factory=time.time)
    stable_since: float | None = None
    normal_streak: int = 0
    transfers_executed: int = 0
    manual_transfers: int = 0
    aborted_transients: int = 0
    # Após cancelamento pelo operador, a mesma perturbação não rearma a chave até a rede normalizar.
    rearm_blocked: bool = False
    load_shed: bool = False
    events: list[TransferEvent] = field(default_factory=list)
    last_tick: float = field(default_factory=time.time)
    pending_reason: str = ""

    @property
    def can_arm(self) -> bool:
        return self.state == "GRID" and not self.rearm_blocked

    def _record(self, event: TransferEvent) -> TransferEvent:
        self.events.append(event)
        self.events = self.events[-50:]
        return event

    def _go(self, new_state: str, reason: str, now: float) -> TransferEvent:
        event = TransferEvent(now, self.state, new_state, reason)
        self.state = new_state
        self.state_since = now
        if new_state == "GRID":
            self.load_shed = False
        return self._record(event)

    def _load_factor(self) -> float:
        if self.state not in ("BACKUP", "RESTORING"):
            return 1.0
        # Com corte de cargas, só as críticas cobertas pela geração solar permanecem ligadas.
        return 0.45 if self.load_shed else 0.6

    def observe(
        self, severity: str, headline: str, now: float | None = None, countdown_s: float | None = None
    ) -> TransferEvent | None:
        """Recebe o veredito da IA a cada pacote de telemetria."""
        now = now or time.time()
        grid_ok = severity in ("NORMAL", "INFO")
        self.normal_streak = self.normal_streak + 1 if grid_ok else 0
        if grid_ok:
            self.rearm_blocked = False

        if self.can_arm and severity == "CRITICAL":
            self.armed_at = now
            self.active_countdown_s = countdown_s or self.countdown_s
            self.pending_reason = headline
            return self._go("ARMED", headline, now)

        if self.state == "ARMED" and self.normal_streak >= self.abort_after_normal_samples:
            self.armed_at = None
            self.aborted_transients += 1
            return self._go("GRID", "Distúrbio transitório normalizado. Comutação abortada.", now)

        if self.state == "BACKUP":
            if grid_ok:
                self.stable_since = self.stable_since or now
            else:
                self.stable_since = None

        if self.state == "RESTORING" and not grid_ok:
            self.stable_since = None
            return self._go("BACKUP", "Nova instabilidade durante o sincronismo. Retorno cancelado.", now)

        return None

    def tick(self, now: float | None = None) -> TransferEvent | None:
        """Avança temporizadores e o estado da bateria (chamado periodicamente)."""
        now = now or time.time()
        dt = max(0.0, now - self.last_tick)
        self.last_tick = now
        self._update_battery(dt)

        if self.state == "ARMED" and self.armed_at and now - self.armed_at >= self.active_countdown_s:
            self.armed_at = None
            self.transfers_executed += 1
            self.stable_since = None
            return self._go("BACKUP", "Planta isolada da rede. Cargas críticas em autoprodução.", now)

        if self.state == "BACKUP" and not self.load_shed and self.battery_soc <= self.low_battery_pct:
            self.load_shed = True
            reason = f"Bateria em {self.battery_soc:.0f}%: cargas não críticas desligadas para preservar o banco."
            return self._record(TransferEvent(now, self.state, self.state, reason))

        if self.state == "BACKUP" and self.stable_since and now - self.stable_since >= self.restore_after_stable_s:
            return self._go("RESTORING", "Rede estável. Iniciando sincronismo para retorno.", now)

        if self.state == "RESTORING" and now - self.state_since >= self.sync_duration_s:
            self.stable_since = None
            return self._go("GRID", "Retorno à rede concluído com segurança.", now)

        return None

    def _update_battery(self, dt: float) -> None:
        # Demonstração acelerada: 1 s simulado = 1 min de operação real.
        hours = dt * 60.0 / 3600.0
        if self.state in ("BACKUP", "RESTORING"):
            deficit_kw = max(0.0, self.plant_load_kw * self._load_factor() - self.solar_peak_kw * 0.7)
            self.battery_soc -= deficit_kw * hours / self.battery_capacity_kwh * 100.0
        else:
            self.battery_soc += 60.0 * hours / self.battery_capacity_kwh * 100.0
        self.battery_soc = max(0.0, min(100.0, self.battery_soc))

    def cancel(self, now: float | None = None) -> TransferEvent | None:
        now = now or time.time()
        if self.state != "ARMED":
            return None
        self.armed_at = None
        self.rearm_blocked = True
        return self._go("GRID", "Comutação cancelada pelo operador.", now)

    def force_backup(self, now: float | None = None) -> TransferEvent | None:
        now = now or time.time()
        if self.state in ("BACKUP", "RESTORING"):
            return None
        self.armed_at = None
        self.manual_transfers += 1
        self.stable_since = None
        return self._go("BACKUP", "Comutação manual acionada pelo operador.", now)

    def force_restore(self, now: float | None = None) -> TransferEvent | None:
        now = now or time.time()
        if self.state != "BACKUP":
            return None
        return self._go("RESTORING", "Retorno à rede solicitado pelo operador.", now)

    def snapshot(self, now: float | None = None) -> dict:
        now = now or time.time()
        countdown = None
        if self.state == "ARMED" and self.armed_at:
            countdown = round(max(0.0, self.active_countdown_s - (now - self.armed_at)), 1)
        restore_in = None
        if self.state == "BACKUP" and self.stable_since:
            restore_in = round(max(0.0, self.restore_after_stable_s - (now - self.stable_since)), 1)
        on_backup = self.state in ("BACKUP", "RESTORING")
        return {
            "state": self.state,
            "label": STATE_LABELS[self.state],
            "countdown_s": countdown,
            "countdown_total_s": self.active_countdown_s,
            "restore_in_s": restore_in,
            "battery_soc": round(self.battery_soc, 1),
            "solar_kw": round(self.solar_peak_kw * 0.7, 1) if on_backup else 0.0,
            "load_kw": round(self.plant_load_kw * self._load_factor(), 1),
            "load_shed": self.load_shed,
            "rearm_blocked": self.rearm_blocked,
            "transfers_executed": self.transfers_executed,
            "manual_transfers": self.manual_transfers,
            "aborted_transients": self.aborted_transients,
            "pending_reason": self.pending_reason if self.state == "ARMED" else "",
            "events": [e.as_dict() for e in self.events[-10:]][::-1],
        }
