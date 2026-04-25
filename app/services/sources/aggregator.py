from __future__ import annotations

import logging

from app.models.schemas import EvidenceChunk
from app.observability import log_event, log_exception
from app.services.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


class SourceAggregator:
    def __init__(self, adapters: list[SourceAdapter]) -> None:
        self.adapters = adapters

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        combined: list[EvidenceChunk] = []
        seen: set[tuple[str, str]] = set()
        log_event(logger, logging.INFO, "evidence_aggregation_started", title=title, year=year, adapter_count=len(self.adapters))
        for adapter in self.adapters:
            try:
                chunks = await adapter.fetch_movie_evidence(title, year)
            except Exception:
                log_exception(
                    logger,
                    "evidence_adapter_failed",
                    title=title,
                    year=year,
                    adapter=getattr(adapter, "name", adapter.__class__.__name__),
                )
                continue
            log_event(
                logger,
                logging.INFO,
                "evidence_adapter_completed",
                title=title,
                year=year,
                adapter=getattr(adapter, "name", adapter.__class__.__name__),
                chunks=len(chunks),
            )
            for chunk in chunks:
                fingerprint = (chunk.source_name, chunk.text.strip())
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                combined.append(chunk)
        log_event(logger, logging.INFO, "evidence_aggregation_completed", title=title, year=year, combined_chunks=len(combined))
        return combined
