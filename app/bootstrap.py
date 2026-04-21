from __future__ import annotations

from app.config import settings
from app.services.cache.repository import CacheRepository
from app.services.pipeline import ExplainPipeline
from app.services.sources.aggregator import SourceAggregator
from app.services.sources.omdb_adapter import OMDbSourceAdapter
from app.services.sources.tmdb_adapter import TMDbSourceAdapter
from app.services.sources.wikipedia_adapter import WikipediaSourceAdapter


def build_pipeline() -> ExplainPipeline:
    adapters = [
        WikipediaSourceAdapter(),
        OMDbSourceAdapter(settings.omdb_api_key),
        TMDbSourceAdapter(settings.tmdb_api_token),
    ]
    aggregator = SourceAggregator(adapters=adapters)
    cache = None
    if settings.cache_enabled:
        cache = CacheRepository(
            db_path=settings.cache_db_path,
            ttl_seconds=settings.cache_ttl_seconds,
        )
    return ExplainPipeline(source_aggregator=aggregator, cache=cache)
