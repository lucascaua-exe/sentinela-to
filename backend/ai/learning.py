"""
Sentinela TO - Aprendizado ativo do copiloto (human-in-the-loop)

Quando o diagnóstico fica abaixo do limiar de confiança, o evento entra na fila de
lacunas de conhecimento (knowledge_gaps). Um engenheiro valida a causa e as ações no
painel; a resolução é gravada (validated_resolutions) e vira um trecho da base RAG,
reindexada em tempo de execução, sem reiniciar o backend.

Lacunas abertas com a mesma assinatura (código da causa raiz) são agrupadas: novas
ocorrências só incrementam o contador, para a fila não ser inundada durante um episódio.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from backend.ai.knowledge_base import VALIDATED_CATEGORY, Chunk

VALIDATED_SOURCE = "Ocorrências validadas pela engenharia (Sentinela TO)"
VALIDATED_TAG = "VALIDATED"

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    codes TEXT NOT NULL,
    site_id TEXT NOT NULL,
    city TEXT NOT NULL,
    diagnosis_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    s_vector REAL NOT NULL,
    s_density REAL NOT NULL,
    measurement TEXT NOT NULL,
    sample TEXT NOT NULL,
    retrieved TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    suggested_immediate TEXT NOT NULL,
    suggested_preventive TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    occurrences INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    closed_by TEXT,
    closed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_gaps_open ON knowledge_gaps(status, signature);
CREATE TABLE IF NOT EXISTS validated_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id INTEGER NOT NULL REFERENCES knowledge_gaps(id),
    codes TEXT NOT NULL,
    cause TEXT NOT NULL,
    immediate TEXT NOT NULL,
    preventive TEXT NOT NULL,
    norm_reference TEXT NOT NULL,
    city TEXT NOT NULL,
    measurement TEXT NOT NULL,
    validated_by TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

GAP_FIELDS_JSON = ("codes", "sample", "retrieved", "suggested_immediate", "suggested_preventive")


class GapNotFound(LookupError):
    pass


class KnowledgeCuration:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _gap(row: sqlite3.Row) -> dict:
        gap = dict(row)
        for key in GAP_FIELDS_JSON:
            gap[key] = json.loads(gap[key])
        return gap

    def record_gap(self, diagnosis: dict, codes: list[str], sample: dict, city: str) -> dict:
        """Registra a lacuna (ou soma uma ocorrência à lacuna aberta de mesma assinatura)."""
        signature = codes[0] if codes else "AI_PATTERN"
        now = time.time()
        with self._db() as db:
            row = db.execute(
                "SELECT id FROM knowledge_gaps WHERE status = 'OPEN' AND signature = ? ORDER BY id LIMIT 1",
                (signature,),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE knowledge_gaps SET occurrences = occurrences + 1, last_seen_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                gap_id = row["id"]
            else:
                solutions = diagnosis["recommended_solutions"]
                gap_id = db.execute(
                    """
                    INSERT INTO knowledge_gaps (
                        signature, codes, site_id, city, diagnosis_id, confidence, s_vector, s_density,
                        measurement, sample, retrieved, hypothesis, suggested_immediate, suggested_preventive,
                        created_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signature,
                        json.dumps(codes),
                        diagnosis["site"],
                        city,
                        diagnosis["id"],
                        diagnosis["confidence_score"],
                        diagnosis["confidence_breakdown"]["vector"],
                        diagnosis["confidence_breakdown"]["density"],
                        diagnosis.get("measurement", ""),
                        json.dumps({k: v for k, v in sample.items() if k != "analysis"}),
                        json.dumps([s["ref"] for s in diagnosis.get("sources", [])]),
                        diagnosis["hypothesis"],
                        json.dumps([s["action"] for s in solutions if s["type"] == "IMEDIATA" and not s.get("contingency")]),
                        json.dumps([s["action"] for s in solutions if s["type"] == "PREVENTIVA" and not s.get("contingency")]),
                        now,
                        now,
                    ),
                ).lastrowid
            gap = db.execute("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)).fetchone()
        return self._gap(gap)

    def list_gaps(self, status: str | None = "OPEN", limit: int = 50) -> list[dict]:
        with self._db() as db:
            if status:
                rows = db.execute(
                    "SELECT * FROM knowledge_gaps WHERE status = ? ORDER BY last_seen_at DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM knowledge_gaps ORDER BY last_seen_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._gap(r) for r in rows]

    def get_gap(self, gap_id: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,)).fetchone()
        if row is None:
            raise GapNotFound(gap_id)
        return self._gap(row)

    def resolve(
        self,
        gap_id: int,
        cause: str,
        immediate: list[str],
        preventive: list[str],
        norm_reference: str,
        user: str,
    ) -> int:
        gap = self.get_gap(gap_id)
        if gap["status"] != "OPEN":
            raise ValueError("Esta lacuna já foi encerrada.")
        now = time.time()
        with self._db() as db:
            resolution_id = db.execute(
                """
                INSERT INTO validated_resolutions (
                    gap_id, codes, cause, immediate, preventive, norm_reference, city, measurement, validated_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gap_id,
                    json.dumps(gap["codes"]),
                    cause,
                    json.dumps(immediate),
                    json.dumps(preventive),
                    norm_reference,
                    gap["city"],
                    gap["measurement"],
                    user,
                    now,
                ),
            ).lastrowid
            db.execute(
                "UPDATE knowledge_gaps SET status = 'RESOLVED', closed_by = ?, closed_at = ? WHERE id = ?",
                (user, now, gap_id),
            )
        return resolution_id

    def dismiss(self, gap_id: int, user: str) -> None:
        gap = self.get_gap(gap_id)
        if gap["status"] != "OPEN":
            raise ValueError("Esta lacuna já foi encerrada.")
        with self._db() as db:
            db.execute(
                "UPDATE knowledge_gaps SET status = 'DISMISSED', closed_by = ?, closed_at = ? WHERE id = ?",
                (user, time.time(), gap_id),
            )

    def counts(self) -> dict:
        with self._db() as db:
            rows = db.execute("SELECT status, COUNT(*) AS n FROM knowledge_gaps GROUP BY status").fetchall()
            validated = db.execute("SELECT COUNT(*) FROM validated_resolutions").fetchone()[0]
        return {**{r["status"].lower(): r["n"] for r in rows}, "validated": validated}

    def validated_chunks(self, code_terms: dict[str, str]) -> list[Chunk]:
        """Resoluções validadas no formato de trecho da base (entram no índice vetorial)."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM validated_resolutions ORDER BY id").fetchall()
        chunks = []
        for row in rows:
            codes = json.loads(row["codes"])
            norm = row["norm_reference"] or "engenharia de campo"
            terms = " ".join(code_terms.get(code, "") for code in codes)
            chunks.append(
                Chunk(
                    id=f"validated#{row['id']}",
                    source=VALIDATED_SOURCE,
                    category=VALIDATED_CATEGORY,
                    title=f"Ocorrência validada #{row['id']}",
                    ref=f"Ocorrência validada #{row['id']} — {norm}",
                    tags=[*codes, VALIDATED_TAG],
                    text=(
                        f"{row['cause']} Medição de referência em {row['city']}: {row['measurement'] or 'não registrada'}. "
                        f"Fundamentação: {norm}. Termos do distúrbio: {terms}. Validado por {row['validated_by']}."
                    ),
                    cause=row["cause"],
                    immediate=json.loads(row["immediate"]),
                    preventive=json.loads(row["preventive"]),
                )
            )
        return chunks
