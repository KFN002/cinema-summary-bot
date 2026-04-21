from __future__ import annotations

from app.models.schemas import ExplainResponse, UserQuery
from app.services.cache.repository import CacheRepository
from app.services.llm.summarizer import GroundedSummarizer
from app.services.search import MovieSearchService
from app.services.sources.aggregator import SourceAggregator


class ExplainPipeline:
    def __init__(self, source_aggregator: SourceAggregator, cache: CacheRepository | None = None) -> None:
        self.search_service = MovieSearchService()
        self.source_aggregator = source_aggregator
        self.summarizer = GroundedSummarizer()
        self.cache = cache

    async def run(self, query: UserQuery) -> ExplainResponse:
        candidates = await self.search_service.search(query.title)
        if not candidates:
            return ExplainResponse(query=query, candidates=[], requires_disambiguation=False)

        top = candidates[0]
        if len(candidates) > 1 and top.confidence < 0.86:
            return ExplainResponse(query=query, candidates=candidates, requires_disambiguation=True)

        if self.cache:
            self.cache.purge_expired()
            cached = self.cache.get_explanation(self._explanation_cache_key(top.title, top.year, query))
            if cached:
                return ExplainResponse(query=query, candidates=[top], explanation=cached, requires_disambiguation=False)

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
        return ExplainResponse(query=query, candidates=[top], explanation=explanation, requires_disambiguation=False)

    async def _get_evidence(self, title: str, year: int | None) -> list:
        if self.cache:
            cache_key = self._evidence_cache_key(title, year)
            cached = self.cache.get_evidence(cache_key)
            if cached is not None:
                return cached

        evidence = await self.source_aggregator.fetch_movie_evidence(title, year)
        if self.cache:
            self.cache.put_evidence(self._evidence_cache_key(title, year), evidence)
        return evidence

    @staticmethod
    def _explanation_cache_key(title: str, year: int | None, query: UserQuery) -> str:
        return "|".join(
            [
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
        return "|".join(["evidence", title, str(year or "")])
