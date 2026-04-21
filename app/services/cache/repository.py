from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.models.schemas import EvidenceChunk, MovieExplanation


class CacheRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS movie_summaries (
                    movie_id TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    year INTEGER,
                    summary_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get_summary(self, movie_id: str) -> MovieExplanation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary_json FROM movie_summaries WHERE movie_id = ?",
                (movie_id,),
            ).fetchone()

        if not row:
            return None

        payload = json.loads(row["summary_json"])
        payload["from_cache"] = True
        payload["evidence"] = [EvidenceChunk(**chunk) for chunk in payload.get("evidence", [])]
        return MovieExplanation(**payload)

    def upsert_summary(self, movie_id: str, explanation: MovieExplanation) -> None:
        serialized = explanation.model_dump(mode="json")
        serialized["evidence"] = [chunk.model_dump(mode="json") for chunk in explanation.evidence]

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO movie_summaries(movie_id, canonical_title, year, summary_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(movie_id) DO UPDATE SET
                    canonical_title = excluded.canonical_title,
                    year = excluded.year,
                    summary_json = excluded.summary_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    movie_id,
                    explanation.canonical_title,
                    explanation.year,
                    json.dumps(serialized),
                ),
            )
