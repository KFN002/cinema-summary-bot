from app.models.schemas import MovieExplanation
from app.services.cache.repository import CacheRepository


def test_cache_roundtrip(tmp_path):
    repo = CacheRepository(str(tmp_path / "cache.db"), ttl_seconds=60)
    explanation = MovieExplanation(
        canonical_title="Inception",
        year=2010,
        summary="summary",
        ending_explained="ending",
        hidden_details="details",
        interpretations="interpretations",
        spoiler_level="full",
        evidence=[],
    )

    repo.put_explanation("key", explanation)
    loaded = repo.get_explanation("key")

    assert loaded is not None
    assert loaded.canonical_title == "Inception"
    assert loaded.ending_explained == "ending"
