from app.models.schemas import EvidenceChunk, MovieExplanation
from app.services.cache.repository import CacheRepository


def test_cache_roundtrip(tmp_path):
    db_file = tmp_path / "cache.db"
    repo = CacheRepository(str(db_file))

    explanation = MovieExplanation(
        canonical_title="Inception",
        year=2010,
        summary="A thief enters dreams.",
        ending_explained="The ending is ambiguous.",
        hidden_details="Totem behavior clues.",
        interpretations="Reality vs projection.",
        spoiler_level="light",
        evidence=[
            EvidenceChunk(
                source_name="Wikipedia",
                source_url="https://example.com",
                text="Evidence",
                spoiler=False,
            )
        ],
    )

    repo.upsert_summary("tt1375666", explanation)
    cached = repo.get_summary("tt1375666")

    assert cached is not None
    assert cached.from_cache is True
    assert cached.canonical_title == "Inception"
