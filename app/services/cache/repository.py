from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.models.schemas import EvidenceChunk, MovieExplanation


class CacheRepository:
    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = ttl_seconds
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS explanation_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get_explanation(self, cache_key: str) -> MovieExplanation | None:
        row = self._get_row("explanation_cache", cache_key)
        if not row:
            return None

        payload = json.loads(row["payload_json"])
        payload["evidence"] = [EvidenceChunk(**chunk) for chunk in payload.get("evidence", [])]
        return MovieExplanation(**payload)

    def put_explanation(self, cache_key: str, explanation: MovieExplanation) -> None:
        payload = explanation.model_dump(mode="json")
        payload["evidence"] = [chunk.model_dump(mode="json") for chunk in explanation.evidence]
        self._put_row("explanation_cache", cache_key, payload)

    def get_evidence(self, cache_key: str) -> list[EvidenceChunk] | None:
        row = self._get_row("evidence_cache", cache_key)
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        return [EvidenceChunk(**chunk) for chunk in payload]

    def put_evidence(self, cache_key: str, evidence: list[EvidenceChunk]) -> None:
        payload = [chunk.model_dump(mode="json") for chunk in evidence]
        self._put_row("evidence_cache", cache_key, payload)

    def _get_row(self, table: str, cache_key: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT payload_json
                FROM {table}
                WHERE cache_key = ?
                  AND created_at >= datetime('now', ?)
                """,
                (cache_key, f"-{self.ttl_seconds} seconds"),
            ).fetchone()

    def _put_row(self, table: str, cache_key: str, payload: object) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {table}(cache_key, payload_json, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = CURRENT_TIMESTAMP
                """,
                (cache_key, json.dumps(payload)),
            )

    def purge_expired(self) -> None:
        with self._connect() as conn:
            for table in ("explanation_cache", "evidence_cache"):
                conn.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE created_at < datetime('now', ?)
                    """,
                    (f"-{self.ttl_seconds} seconds",),
                )
