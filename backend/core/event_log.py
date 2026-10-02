"""
Histórico persistente de ocorrências (SQLite).

Uma ocorrência (episode) vai do primeiro alerta de atenção/crítico até a rede ficar estável
de novo. Guarda pico de severidade, distúrbios, extremos medidos, comutação para autoprodução
e o diagnóstico da IA. Os alertas da IA e as ações da chave de transferência entram como
eventos ligados à ocorrência e formam a linha do tempo.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id              TEXT    NOT NULL,
    started_at           REAL    NOT NULL,
    ended_at             REAL,
    peak_severity        TEXT    NOT NULL,
    primary_code         TEXT    NOT NULL,
    codes                TEXT    NOT NULL DEFAULT '[]',
    title                TEXT    NOT NULL,
    min_voltage          REAL,
    max_voltage          REAL,
    max_current          REAL,
    min_power_factor     REAL,
    max_thd              REAL,
    transferred          INTEGER NOT NULL DEFAULT 0,
    diagnosis_cause      TEXT,
    diagnosis_confidence REAL,
    low_confidence       INTEGER NOT NULL DEFAULT 0,
    measurement          TEXT,
    actions              TEXT    NOT NULL DEFAULT '[]',
    norm_references      TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_episodes_started ON episodes(started_at);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
    site_id    TEXT    NOT NULL,
    ts         REAL    NOT NULL,
    level      TEXT    NOT NULL,
    source     TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    detail     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_episode ON events(episode_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

JSON_FIELDS = ("codes", "actions", "norm_references")


@dataclass
class EpisodeFilter:
    start: float | None = None
    end: float | None = None
    sites: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    transferred: bool | None = None
    query: str = ""

    def where(self) -> tuple[str, list]:
        clauses, params = [], []
        if self.start is not None:
            clauses.append("COALESCE(ended_at, ?) >= ?")
            params += [time.time(), self.start]
        if self.end is not None:
            clauses.append("started_at <= ?")
            params.append(self.end)
        if self.sites:
            clauses.append(f"site_id IN ({','.join('?' * len(self.sites))})")
            params += self.sites
        if self.severities:
            clauses.append(f"peak_severity IN ({','.join('?' * len(self.severities))})")
            params += self.severities
        if self.codes:
            # Casa a ocorrência que teve qualquer um dos distúrbios, não só o principal.
            clauses.append("(" + " OR ".join("codes LIKE ?" for _ in self.codes) + ")")
            params += [f'%"{code}"%' for code in self.codes]
        if self.transferred is not None:
            clauses.append("transferred = ?")
            params.append(int(self.transferred))
        if self.query:
            clauses.append("(title LIKE ? OR COALESCE(diagnosis_cause, '') LIKE ?)")
            params += [f"%{self.query}%"] * 2
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _episode(row: sqlite3.Row, now: float | None = None) -> dict:
        ep = dict(row)
        for key in JSON_FIELDS:
            ep[key] = json.loads(ep[key])
        ep["transferred"] = bool(ep["transferred"])
        ep["low_confidence"] = bool(ep["low_confidence"])
        ep["ongoing"] = ep["ended_at"] is None
        ep["duration_s"] = round((ep["ended_at"] or now or time.time()) - ep["started_at"], 1)
        return ep

    def close_dangling(self) -> int:
        """Ocorrências deixadas abertas por um desligamento do backend terminam no último evento registrado."""
        with self._db() as db:
            cur = db.execute(
                """
                UPDATE episodes SET ended_at = COALESCE(
                    (SELECT MAX(ts) FROM events WHERE events.episode_id = episodes.id), started_at
                ) WHERE ended_at IS NULL
                """
            )
            return cur.rowcount

    def start_episode(self, site_id: str, ts: float, severity: str, code: str, title: str) -> int:
        with self._db() as db:
            return db.execute(
                "INSERT INTO episodes (site_id, started_at, peak_severity, primary_code, codes, title) VALUES (?, ?, ?, ?, ?, ?)",
                (site_id, ts, severity, code, json.dumps([code]), title),
            ).lastrowid

    def escalate(self, episode_id: int, severity: str, code: str, title: str) -> None:
        """Novo distúrbio ou severidade maior: o título e o código principal acompanham o pico."""
        with self._db() as db:
            row = db.execute("SELECT peak_severity, codes FROM episodes WHERE id = ?", (episode_id,)).fetchone()
            if row is None:
                return
            codes = json.loads(row["codes"])
            if code not in codes:
                codes.append(code)
            if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(row["peak_severity"], 0):
                db.execute(
                    "UPDATE episodes SET peak_severity = ?, primary_code = ?, title = ?, codes = ? WHERE id = ?",
                    (severity, code, title, json.dumps(codes), episode_id),
                )
            else:
                db.execute("UPDATE episodes SET codes = ? WHERE id = ?", (json.dumps(codes), episode_id))

    def attach_diagnosis(self, episode_id: int, diagnosis: dict) -> None:
        actions = [
            {"type": s["type"], "action": s["action"]}
            for s in diagnosis.get("recommended_solutions", [])
            if not s.get("contingency")
        ]
        cause = diagnosis.get("hypothesis") or diagnosis.get("cause_analysis", "")
        with self._db() as db:
            db.execute(
                """
                UPDATE episodes SET diagnosis_cause = ?, diagnosis_confidence = ?, low_confidence = ?,
                    measurement = ?, actions = ?, norm_references = ? WHERE id = ?
                """,
                (
                    cause,
                    diagnosis.get("confidence_score"),
                    int(bool(diagnosis.get("low_confidence"))),
                    diagnosis.get("measurement", ""),
                    json.dumps(actions, ensure_ascii=False),
                    json.dumps(diagnosis.get("norm_references", []), ensure_ascii=False),
                    episode_id,
                ),
            )

    def mark_transferred(self, episode_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE episodes SET transferred = 1 WHERE id = ?", (episode_id,))

    def end_episode(self, episode_id: int, ended_at: float, extremes: dict) -> None:
        with self._db() as db:
            db.execute(
                """
                UPDATE episodes SET ended_at = ?, min_voltage = ?, max_voltage = ?, max_current = ?,
                    min_power_factor = ?, max_thd = ? WHERE id = ?
                """,
                (
                    ended_at,
                    extremes.get("min_voltage"),
                    extremes.get("max_voltage"),
                    extremes.get("max_current"),
                    extremes.get("min_power_factor"),
                    extremes.get("max_thd"),
                    episode_id,
                ),
            )

    def update_extremes(self, episode_id: int, extremes: dict) -> None:
        with self._db() as db:
            db.execute(
                """
                UPDATE episodes SET min_voltage = ?, max_voltage = ?, max_current = ?, min_power_factor = ?, max_thd = ?
                WHERE id = ?
                """,
                (
                    extremes.get("min_voltage"),
                    extremes.get("max_voltage"),
                    extremes.get("max_current"),
                    extremes.get("min_power_factor"),
                    extremes.get("max_thd"),
                    episode_id,
                ),
            )

    def record_event(
        self, site_id: str, episode_id: int | None, ts: float, level: str, source: str, title: str, detail: str = ""
    ) -> int:
        with self._db() as db:
            return db.execute(
                "INSERT INTO events (episode_id, site_id, ts, level, source, title, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (episode_id, site_id, ts, level, source, title, detail),
            ).lastrowid

    def episodes(self, flt: EpisodeFilter, limit: int = 200, offset: int = 0) -> tuple[list[dict], int]:
        where, params = flt.where()
        now = time.time()
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM episodes{where}", params).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM episodes{where} ORDER BY started_at DESC LIMIT ? OFFSET ?", [*params, limit, offset]
            ).fetchall()
        return [self._episode(r, now) for r in rows], total

    def episode(self, episode_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
            if row is None:
                return None
            events = db.execute(
                "SELECT ts, level, source, title, detail FROM events WHERE episode_id = ? ORDER BY ts", (episode_id,)
            ).fetchall()
        ep = self._episode(row)
        ep["events"] = [dict(e) for e in events]
        return ep

    def stats(self, flt: EpisodeFilter) -> dict:
        where, params = flt.where()
        now = time.time()
        with self._db() as db:
            rows = db.execute(
                f"SELECT site_id, started_at, ended_at, peak_severity, primary_code, transferred FROM episodes{where}",
                params,
            ).fetchall()
        by_site: dict[str, int] = {}
        by_code: dict[str, int] = {}
        by_day: dict[str, dict[str, int]] = {}
        total_s = 0.0
        for r in rows:
            by_site[r["site_id"]] = by_site.get(r["site_id"], 0) + 1
            by_code[r["primary_code"]] = by_code.get(r["primary_code"], 0) + 1
            day = time.strftime("%Y-%m-%d", time.localtime(r["started_at"]))
            bucket = by_day.setdefault(day, {"CRITICAL": 0, "WARNING": 0})
            bucket[r["peak_severity"]] = bucket.get(r["peak_severity"], 0) + 1
            total_s += (r["ended_at"] or now) - r["started_at"]
        return {
            "total": len(rows),
            "critical": sum(1 for r in rows if r["peak_severity"] == "CRITICAL"),
            "warning": sum(1 for r in rows if r["peak_severity"] == "WARNING"),
            "transfers": sum(1 for r in rows if r["transferred"]),
            "total_duration_s": round(total_s, 1),
            "by_site": by_site,
            "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
            "by_day": dict(sorted(by_day.items())),
        }
