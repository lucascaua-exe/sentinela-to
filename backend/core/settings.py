"""
Preferências do painel por usuário (SQLite): voz, alertas, exibição e cabeçalho dos relatórios.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    username   TEXT PRIMARY KEY COLLATE NOCASE,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auto_voice: bool = False
    voice_on_warning: bool = False
    voice_name: str = Field(default="", max_length=120)
    voice_rate: float = Field(default=1.0, ge=0.7, le=1.5)
    voice_volume: float = Field(default=1.0, ge=0.0, le=1.0)
    alert_sound: bool = False
    browser_notifications: bool = False
    default_site: str = Field(default="", max_length=40)
    show_simulator: bool = True
    report_company: str = Field(default="", max_length=120)
    report_responsible: str = Field(default="", max_length=120)

    @field_validator("voice_name", "default_site", "report_company", "report_responsible")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, username: str) -> UserSettings:
        with self._db() as db:
            row = db.execute("SELECT data FROM user_settings WHERE username = ?", (username,)).fetchone()
        if row is None:
            return UserSettings()
        try:
            return UserSettings.model_validate(json.loads(row[0]))
        except (ValueError, ValidationError):
            return UserSettings()

    def update(self, username: str, changes: dict) -> UserSettings:
        """Aplica só os campos enviados sobre as preferências atuais (validação do conjunto completo)."""
        merged = UserSettings.model_validate({**self.get(username).model_dump(), **changes})
        with self._db() as db:
            db.execute(
                """
                INSERT INTO user_settings (username, data, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                """,
                (username, merged.model_dump_json(), time.time()),
            )
        return merged
