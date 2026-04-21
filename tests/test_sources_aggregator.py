import pytest

from app.models.schemas import EvidenceChunk
from app.services.sources.aggregator import SourceAggregator


class OkAdapter:
    name = "ok"

    async def fetch_movie_evidence(self, title: str, year: int | None = None):
        return [EvidenceChunk(source_name="ok", source_url="https://ok", text=f"{title}-{year}", spoiler=False)]


class FailingAdapter:
    name = "bad"

    async def fetch_movie_evidence(self, title: str, year: int | None = None):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_aggregator_skips_failed_adapter():
    aggregator = SourceAggregator([FailingAdapter(), OkAdapter()])
    chunks = await aggregator.fetch_movie_evidence("Inception", 2010)
    assert len(chunks) == 1
    assert chunks[0].source_name == "ok"


class DuplicateAdapter:
    name = "dupe"

    async def fetch_movie_evidence(self, title: str, year: int | None = None):
        return [EvidenceChunk(source_name="ok", source_url="https://ok", text=f"{title}-{year}", spoiler=False)]


@pytest.mark.asyncio
async def test_aggregator_deduplicates_same_source_and_text():
    aggregator = SourceAggregator([OkAdapter(), DuplicateAdapter()])
    chunks = await aggregator.fetch_movie_evidence("Inception", 2010)
    assert len(chunks) == 1
