from __future__ import annotations

import logging

from app.models.schemas import ExplainResponse, UserQuery
from app.observability import log_event, log_exception
from app.services.cache.repository import CacheRepository
from app.services.llm.summarizer import GroundedSummarizer
from app.services.search import MovieSearchService
from app.services.sources.aggregator import SourceAggregator

logger = logging.getLogger(__name__)


class ExplainPipeline:
    CACHE_SCHEMA_VERSION = "v2"

    def __init__(self, source_aggregator: SourceAggregator, cache: CacheRepository | None = None) -> None:
        self.search_service = MovieSearchService()
        self.source_aggregator = source_aggregator
        self.summarizer = GroundedSummarizer()
        self.cache = cache

    async def run(self, query: UserQuery) -> ExplainResponse:
        log_event(
            logger,
            logging.INFO,
            "pipeline_run_started",
            title=query.title,
            mode=query.mode,
            watched=query.watched,
            detail_level=query.detail_level,
            focus_section=query.focus_section,
        )
        try:
            candidates = await self.search_service.search(query.title)
            if not candidates:
                log_event(logger, logging.INFO, "pipeline_no_candidates", title=query.title)
                return ExplainResponse(query=query, candidates=[], requires_disambiguation=False)

            top = candidates[0]
            if len(candidates) > 1 and top.confidence < 0.86:
                log_event(
                    logger,
                    logging.INFO,
                    "pipeline_disambiguation_required",
                    title=query.title,
                    top_candidate=top.title,
                    confidence=top.confidence,
                    candidate_count=len(candidates),
                )
                return ExplainResponse(query=query, candidates=candidates, requires_disambiguation=True)

            if self.cache:
                self.cache.purge_expired()
                cache_key = self._explanation_cache_key(top.title, top.year, query)
                cached = self.cache.get_explanation(cache_key)
                if cached:
                    log_event(
                        logger,
                        logging.INFO,
                        "pipeline_explanation_cache_hit",
                        title=top.title,
                        year=top.year,
                    )
                    return ExplainResponse(query=query, candidates=[top], explanation=cached, requires_disambiguation=False)
                log_event(
                    logger,
                    logging.INFO,
                    "pipeline_explanation_cache_miss",
                    title=top.title,
                    year=top.year,
                )

            evidence = await self._get_evidence(top.title, top.year)
            explanation = await self.summarizer.summarize(
                title=top.title,
                year=top.year,
                evidence=evidence,
                allow_spoilers=query.allow_spoilers,
                watched=query.watched,
                detail_level=query.detail_level,
                focus_section=query.focus_section,
            )
            if self.cache:
                self.cache.put_explanation(self._explanation_cache_key(top.title, top.year, query), explanation)
                log_event(
                    logger,
                    logging.INFO,
                    "pipeline_explanation_cached",
                    title=top.title,
                    year=top.year,
                )

            log_event(
                logger,
                logging.INFO,
                "pipeline_run_completed",
                title=top.title,
                year=top.year,
                evidence_chunks=len(evidence),
                spoiler_level=explanation.spoiler_level,
            )
            return ExplainResponse(query=query, candidates=[top], explanation=explanation, requires_disambiguation=False)
        except Exception:
            log_exception(
                logger,
                "pipeline_run_failed",
                title=query.title,
                mode=query.mode,
                watched=query.watched,
                detail_level=query.detail_level,
            )
            raise

    async def _get_evidence(self, title: str, year: int | None) -> list:
        if self.cache:
            cache_key = self._evidence_cache_key(title, year)
            cached = self.cache.get_evidence(cache_key)
            if cached is not None:
                log_event(
                    logger,
                    logging.INFO,
                    "pipeline_evidence_cache_hit",
                    title=title,
                    year=year,
                    evidence_chunks=len(cached),
                )
                return cached
            log_event(logger, logging.INFO, "pipeline_evidence_cache_miss", title=title, year=year)

        evidence = await self.source_aggregator.fetch_movie_evidence(title, year)
        if self.cache:
            self.cache.put_evidence(self._evidence_cache_key(title, year), evidence)
            log_event(
                logger,
                logging.INFO,
                "pipeline_evidence_cached",
                title=title,
                year=year,
                evidence_chunks=len(evidence),
            )
        return evidence

    @staticmethod
    def _explanation_cache_key(title: str, year: int | None, query: UserQuery) -> str:
        return "|".join(
            [
                ExplainPipeline.CACHE_SCHEMA_VERSION,
                "explanation",
                title,
                str(year or ""),
                query.mode,
                str(query.allow_spoilers),
                str(query.watched),
                query.detail_level,
                query.focus_section or "",
            ]
        )

    @staticmethod
    def _evidence_cache_key(title: str, year: int | None) -> str:
        return "|".join([ExplainPipeline.CACHE_SCHEMA_VERSION, "evidence", title, str(year or "")])
