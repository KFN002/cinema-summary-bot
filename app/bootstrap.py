from __future__ import annotations

import logging

from app.config import settings
from app.observability import log_event
from app.services.cache.repository import CacheRepository
from app.services.pipeline import ExplainPipeline
from app.services.sources.aggregator import SourceAggregator
from app.services.sources.omdb_adapter import OMDbSourceAdapter
from app.services.sources.tmdb_adapter import TMDbSourceAdapter
from app.services.sources.wikipedia_adapter import WikipediaSourceAdapter

logger = logging.getLogger(__name__)


def build_pipeline() -> ExplainPipeline:
    adapters = [WikipediaSourceAdapter()]
    if settings.has_omdb_api_key():
        adapters.append(OMDbSourceAdapter(settings.omdb_api_key))
    if settings.has_tmdb_api_token():
        adapters.append(TMDbSourceAdapter(settings.tmdb_api_token))
    aggregator = SourceAggregator(adapters=adapters)
    cache = None
    if settings.cache_enabled:
        cache = CacheRepository(
            db_path=settings.cache_db_path,
            ttl_seconds=settings.cache_ttl_seconds,
        )
    log_event(
        logger,
        logging.INFO,
        "pipeline_built",
        adapters=[adapter.name for adapter in adapters],
        cache_enabled=bool(cache),
        gigachat_enabled=getattr(settings, "has_gigachat_credentials", lambda: False)(),
    )
    return ExplainPipeline(source_aggregator=aggregator, cache=cache)
