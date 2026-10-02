"""
Autenticação do painel: usuários em SQLite, senhas PBKDF2-SHA256 e sessões por cookie HttpOnly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = Path(os.environ.get("SENTINELA_DB_PATH", DATA_DIR / "sentinela.db"))
DEVICE_TOKEN_PATH = DATA_DIR / "device.token"
DEVICE_TOKEN_HEADER = "X-Device-Token"
SESSION_COOKIE = "sentinela_session"
SESSION_TTL_S = 12 * 3600
PBKDF2_ITERATIONS = 310_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    created_at    REAL    NOT NULL,
    last_login_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_device_token() -> str:
    """Token compartilhado com os medidores IoT: SENTINELA_DEVICE_TOKEN ou data/device.token (gerado na 1ª execução)."""
    token = os.environ.get("SENTINELA_DEVICE_TOKEN")
    if token:
        return token
    if DEVICE_TOKEN_PATH.exists():
        return DEVICE_TOKEN_PATH.read_text(encoding="utf-8").strip()
    DEVICE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    DEVICE_TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


def device_token_valid(presented: str | None, expected: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


# Comparado quando o usuário não existe, para que o tempo de resposta não revele usuários válidos.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class UserStore:
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

    def user_count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def set_password(self, username: str, password: str) -> None:
        """Cria o usuário ou redefine a senha de um existente (revogando suas sessões)."""
        with self._db() as db:
            db.execute(
                """
                INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash
                """,
                (username, hash_password(password), time.time()),
            )
            db.execute(
                "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)",
                (username,),
            )

    def authenticate(self, username: str, password: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        with self._db() as db:
            db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (time.time(), row["id"]))
        return {"id": row["id"], "username": row["username"]}

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_token_hash(token), user_id, now, now + SESSION_TTL_S),
            )
        return token

    def session_user(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._db() as db:
            row = db.execute(
                """
                SELECT u.username FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (_token_hash(token), time.time()),
            ).fetchone()
        return row["username"] if row else None

    def delete_session(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


class LoginThrottle:
    """Limita tentativas de login por cliente numa janela de tempo.

    A tentativa é reservada antes da verificação da senha: requisições paralelas não escapam do limite.
    """

    def __init__(self, max_failures: int = 5, window_s: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, now: float) -> None:
        for client in list(self._attempts):
            attempts = self._attempts[client]
            while attempts and now - attempts[0] > self.window_s:
                attempts.popleft()
            if not attempts:
                del self._attempts[client]

    def attempt(self, client: str) -> bool:
        """Registra uma tentativa; retorna False se o cliente já esgotou o limite."""
        now = time.time()
        self._prune(now)
        attempts = self._attempts[client]
        if len(attempts) >= self.max_failures:
            return False
        attempts.append(now)
        return True

    def reset(self, client: str) -> None:
        self._attempts.pop(client, None)
