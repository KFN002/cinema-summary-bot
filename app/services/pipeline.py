from __future__ import annotations

from app.models.schemas import ExplainResponse, UserQuery
from app.services.cache.repository import CacheRepository
from app.services.llm.summarizer import GroundedSummarizer
from app.services.search import MovieSearchService
from app.services.sources.aggregator import SourceAggregator


class ExplainPipeline:
    def __init__(self, cache: CacheRepository, source_aggregator: SourceAggregator) -> None:
        self.search_service = MovieSearchService()
        self.source_aggregator = source_aggregator
        self.summarizer = GroundedSummarizer()
        self.cache = cache

    async def run(self, query: UserQuery) -> ExplainResponse:
        candidates = self.search_service.search(query.title)
        if not candidates:
            return ExplainResponse(query=query, candidates=[], requires_disambiguation=False)

        top = candidates[0]
        if len(candidates) > 1 and top.confidence < 0.86:
            return ExplainResponse(query=query, candidates=candidates, requires_disambiguation=True)

        cached = self.cache.get_summary(top.movie_id)
        if cached:
            return ExplainResponse(query=query, candidates=[top], explanation=cached, requires_disambiguation=False)

        evidence = await self.source_aggregator.fetch_movie_evidence(top.title, top.year)
        explanation = self.summarizer.summarize(
            title=top.title,
            year=top.year,
            evidence=evidence,
            allow_spoilers=query.allow_spoilers,
        )
        self.cache.upsert_summary(top.movie_id, explanation)

        return ExplainResponse(query=query, candidates=[top], explanation=explanation, requires_disambiguation=False)
