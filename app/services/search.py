from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.models.schemas import MovieCandidate


@dataclass
class SearchIndexEntry:
    movie_id: str
    title: str
    year: int | None
    aliases: list[str]


class MovieSearchService:
    def __init__(self) -> None:
        # Seed list for MVP; expand from DB over time.
        self._index: list[SearchIndexEntry] = [
            SearchIndexEntry(
                movie_id="tt1130884",
                title="Shutter Island",
                year=2010,
                aliases=["shutter island", "shutterisland"],
            ),
            SearchIndexEntry(
                movie_id="tt1375666",
                title="Inception",
                year=2010,
                aliases=["inception", "incepcion"],
            ),
            SearchIndexEntry(
                movie_id="tt0133093",
                title="The Matrix",
                year=1999,
                aliases=["matrix", "the matrix"],
            ),
        ]

    @staticmethod
    def normalize_title(title: str) -> str:
        lowered = title.strip().lower()
        normalized = re.sub(r"[^a-z0-9\s]", "", lowered)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def search(self, query: str, top_k: int = 3) -> list[MovieCandidate]:
        norm_query = self.normalize_title(query)
        scored: list[MovieCandidate] = []

        for entry in self._index:
            options = [self.normalize_title(entry.title), *entry.aliases]
            score = max(difflib.SequenceMatcher(None, norm_query, alias).ratio() for alias in options)
            if score >= 0.55:
                scored.append(
                    MovieCandidate(
                        movie_id=entry.movie_id,
                        title=entry.title,
                        year=entry.year,
                        confidence=round(score, 3),
                    )
                )

        scored.sort(key=lambda c: c.confidence, reverse=True)
        return scored[:top_k]
