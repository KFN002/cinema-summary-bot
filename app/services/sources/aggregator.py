from __future__ import annotations

from app.models.schemas import EvidenceChunk
from app.services.sources.base import SourceAdapter


class SourceAggregator:
    def __init__(self, adapters: list[SourceAdapter]) -> None:
        self.adapters = adapters

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        combined: list[EvidenceChunk] = []
        for adapter in self.adapters:
            try:
                combined.extend(await adapter.fetch_movie_evidence(title, year))
            except Exception:
                # MVP behavior: keep pipeline alive when one source fails.
                continue
        return combined
