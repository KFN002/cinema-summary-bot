from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.models.schemas import MovieCandidate


@dataclass
class SearchIndexEntry:
    movie_id: str
    title: str
    year: int | None
    aliases: list[str]


class MovieSearchService:
    WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
    USER_AGENT = "cinema-summary-bot/0.2 (movie search helper)"

    def __init__(self) -> None:
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
            SearchIndexEntry(
                movie_id="tt0137523",
                title="Fight Club",
                year=1999,
                aliases=["fight club"],
            ),
            SearchIndexEntry(
                movie_id="tt0068646",
                title="The Godfather",
                year=1972,
                aliases=["godfather", "the godfather"],
            ),
            SearchIndexEntry(
                movie_id="tt0114369",
                title="Se7en",
                year=1995,
                aliases=["se7en", "seven"],
            ),
            SearchIndexEntry(
                movie_id="tt0816692",
                title="Interstellar",
                year=2014,
                aliases=["interstellar"],
            ),
            SearchIndexEntry(
                movie_id="tt0468569",
                title="The Dark Knight",
                year=2008,
                aliases=["dark knight", "the dark knight"],
            ),
            SearchIndexEntry(
                movie_id="tt0482571",
                title="The Prestige",
                year=2006,
                aliases=["prestige", "the prestige"],
            ),
            SearchIndexEntry(
                movie_id="tt0110912",
                title="Pulp Fiction",
                year=1994,
                aliases=["pulp fiction"],
            ),
            SearchIndexEntry(
                movie_id="tt0109830",
                title="Forrest Gump",
                year=1994,
                aliases=["forrest gump"],
            ),
            SearchIndexEntry(
                movie_id="tt0111161",
                title="The Shawshank Redemption",
                year=1994,
                aliases=["shawshank redemption", "the shawshank redemption", "shawshank"],
            ),
            SearchIndexEntry(
                movie_id="tt0120737",
                title="The Lord of the Rings: The Fellowship of the Ring",
                year=2001,
                aliases=["lord of the rings", "fellowship of the ring", "lotr fellowship"],
            ),
            SearchIndexEntry(
                movie_id="tt0245429",
                title="Spirited Away",
                year=2001,
                aliases=["spirited away"],
            ),
            SearchIndexEntry(
                movie_id="tt6751668",
                title="Parasite",
                year=2019,
                aliases=["parasite"],
            ),
        ]

    @staticmethod
    def normalize_title(title: str) -> str:
        lowered = title.strip().lower()
        normalized = re.sub(r"[^a-z0-9\s]", "", lowered)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    @staticmethod
    def split_title_and_year(query: str) -> tuple[str, int | None]:
        cleaned = query.strip()
        match = re.search(r"(?:^|\s|\()((?:19|20)\d{2})(?:\))?\s*$", cleaned)
        if not match:
            return cleaned, None
        year = int(match.group(1))
        title = cleaned[: match.start(1)].strip(" ()-")
        return (title or cleaned), year

    def search_local(self, query: str, top_k: int = 5) -> list[MovieCandidate]:
        title, year = self.split_title_and_year(query)
        norm_query = self.normalize_title(title)
        scored: list[MovieCandidate] = []

        for entry in self._index:
            options = [self.normalize_title(entry.title), *entry.aliases]
            title_score = max(self._title_similarity(norm_query, alias) for alias in options)
            score = self._final_score(title_score, year, entry.year, source_bonus=0.02)
            if score >= 0.68:
                scored.append(
                    MovieCandidate(
                        movie_id=entry.movie_id,
                        title=entry.title,
                        year=entry.year,
                        confidence=round(min(score, 0.99), 3),
                    )
                )

        scored.sort(key=lambda c: c.confidence, reverse=True)
        return scored[:top_k]

    async def search(self, query: str, top_k: int = 5) -> list[MovieCandidate]:
        title, year = self.split_title_and_year(query)
        merged: dict[str, MovieCandidate] = {}

        for candidate in self.search_local(query, top_k=top_k):
            merged[candidate.movie_id] = candidate

        for candidate in await self._search_omdb(title, year):
            self._merge_candidate(merged, candidate)

        for candidate in await self._search_tmdb(title, year):
            self._merge_candidate(merged, candidate)

        for candidate in await self._search_wikipedia_titles(title, year):
            self._merge_candidate(merged, candidate)

        results = sorted(merged.values(), key=lambda candidate: candidate.confidence, reverse=True)
        return results[:top_k]

    def _merge_candidate(self, merged: dict[str, MovieCandidate], candidate: MovieCandidate) -> None:
        current = merged.get(candidate.movie_id)
        if not current or candidate.confidence > current.confidence:
            merged[candidate.movie_id] = candidate

    def _candidate_from_result(
        self,
        movie_id: str,
        title: str,
        result_year: int | None,
        query_title: str,
        query_year: int | None,
        source_bonus: float,
    ) -> MovieCandidate:
        title_score = self._title_similarity(
            self.normalize_title(query_title),
            self.normalize_title(title),
        )
        score = self._final_score(title_score, query_year, result_year, source_bonus=source_bonus)
        return MovieCandidate(
            movie_id=movie_id,
            title=title,
            year=result_year,
            confidence=round(min(score, 0.995), 3),
        )

    def _final_score(
        self,
        title_score: float,
        query_year: int | None,
        result_year: int | None,
        source_bonus: float,
    ) -> float:
        score = title_score + source_bonus
        if query_year and result_year:
            if query_year == result_year:
                score += 0.15
            elif abs(query_year - result_year) == 1:
                score += 0.05
            else:
                score -= 0.08
        return max(score, 0.0)

    def _title_similarity(self, normalized_query: str, normalized_title: str) -> float:
        if normalized_query == normalized_title:
            return 1.0

        seq_ratio = difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio()
        query_tokens = set(normalized_query.split())
        title_tokens = set(normalized_title.split())
        overlap_ratio = 0.0
        if query_tokens and title_tokens:
            overlap_ratio = len(query_tokens & title_tokens) / max(len(query_tokens), len(title_tokens))

        if not (query_tokens & title_tokens) and seq_ratio < 0.9:
            return seq_ratio * 0.75

        contains_bonus = 0.08 if normalized_query in normalized_title or normalized_title in normalized_query else 0.0
        return min(max(seq_ratio, overlap_ratio + contains_bonus), 1.0)

    async def _search_omdb(self, title: str, year: int | None) -> list[MovieCandidate]:
        if not settings.omdb_api_key or settings.omdb_api_key == "PASTE_OMDB_API_KEY_HERE":
            return []

        params = {
            "apikey": settings.omdb_api_key,
            "s": title,
            "type": "movie",
        }
        if year:
            params["y"] = year

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://www.omdbapi.com/", params=params)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except Exception:
            return []

        if payload.get("Response") == "False":
            return []

        results = []
        for item in payload.get("Search", [])[:5]:
            imdb_id = str(item.get("imdbID", "")).strip()
            result_title = str(item.get("Title", "")).strip()
            result_year = self._parse_year(item.get("Year"))
            if not imdb_id or not result_title:
                continue
            results.append(
                self._candidate_from_result(
                    movie_id=imdb_id,
                    title=result_title,
                    result_year=result_year,
                    query_title=title,
                    query_year=year,
                    source_bonus=0.08,
                )
            )
        return results

    async def _search_tmdb(self, title: str, year: int | None) -> list[MovieCandidate]:
        if not settings.tmdb_api_token or settings.tmdb_api_token == "PASTE_TMDB_API_TOKEN_HERE":
            return []

        headers = {"Authorization": f"Bearer {settings.tmdb_api_token}"}
        params: dict[str, Any] = {"query": title, "include_adult": False}
        if year:
            params["year"] = year

        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                response = await client.get("https://api.themoviedb.org/3/search/movie", params=params)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except Exception:
            return []

        results = []
        for item in payload.get("results", [])[:5]:
            movie_id = item.get("id")
            result_title = str(item.get("title", "")).strip()
            result_year = self._parse_year(item.get("release_date"))
            if not movie_id or not result_title:
                continue
            results.append(
                self._candidate_from_result(
                    movie_id=f"tmdb:{movie_id}",
                    title=result_title,
                    result_year=result_year,
                    query_title=title,
                    query_year=year,
                    source_bonus=0.06,
                )
            )
        return results

    async def _search_wikipedia_titles(self, title: str, year: int | None) -> list[MovieCandidate]:
        search_phrase = f"{title} {year or ''} film".strip()
        params = {
            "action": "opensearch",
            "search": search_phrase,
            "limit": 5,
            "namespace": 0,
            "format": "json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": self.USER_AGENT}) as client:
                response = await client.get(self.WIKIPEDIA_API_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []

        if len(payload) < 2 or not payload[1]:
            return []

        results = []
        for article_title in payload[1][:5]:
            article_title = str(article_title).strip()
            if "film" not in article_title.lower():
                continue
            article_year = self._parse_year(article_title)
            cleaned_title = re.sub(r"\s*\(\d{4}\s+film\)\s*$", "", article_title).strip()
            cleaned_title = re.sub(r"\s*\(film\)\s*$", "", cleaned_title).strip()
            if not cleaned_title:
                continue
            results.append(
                self._candidate_from_result(
                    movie_id=f"wiki:{article_title}",
                    title=cleaned_title,
                    result_year=article_year,
                    query_title=title,
                    query_year=year,
                    source_bonus=0.05,
                )
            )
        return results

    @staticmethod
    def _parse_year(value: Any) -> int | None:
        if not value:
            return None
        match = re.search(r"(19|20)\d{2}", str(value))
        return int(match.group(0)) if match else None
