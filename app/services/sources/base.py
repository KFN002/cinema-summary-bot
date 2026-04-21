from __future__ import annotations

from typing import Protocol

from app.models.schemas import EvidenceChunk


class SourceAdapter(Protocol):
    name: str

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        ...
