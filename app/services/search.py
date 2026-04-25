from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass
from textwrap import shorten
from typing import Any

import httpx

from app.config import settings
from app.models.schemas import MovieCandidate
from app.observability import balance_snapshot, elapsed_ms, log_event, log_exception, sanitize_mapping

logger = logging.getLogger(__name__)


@dataclass
class SearchIndexEntry:
    movie_id: str
    title: str
    year: int | None
    aliases: list[str]


class MovieSearchService:
    WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
    WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
    WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
    USER_AGENT = "cinema-summary-bot/0.2 (movie search helper)"

    def __init__(self) -> None:
        self._omdb_disabled_reason: str | None = None
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
        results = scored[:top_k]
        log_event(
            logger,
            logging.INFO,
            "movie_search_local_completed",
            query=query,
            title=title,
            year=year,
            matches=len(results),
            top_match=results[0].title if results else None,
        )
        return results

    async def search(self, query: str, top_k: int = 5) -> list[MovieCandidate]:
        title, year = self.split_title_and_year(query)
        merged: dict[str, MovieCandidate] = {}
        log_event(logger, logging.INFO, "movie_search_started", query=query, title=title, year=year, top_k=top_k)

        for candidate in self.search_local(query, top_k=top_k):
            merged[candidate.movie_id] = candidate

        for candidate in await self._search_omdb(title, year):
            self._merge_candidate(merged, candidate)

        for candidate in await self._search_tmdb(title, year):
            self._merge_candidate(merged, candidate)

        for candidate in await self._search_wikipedia_titles(title, year):
            self._merge_candidate(merged, candidate)

        for candidate in await self._search_wikidata_titles(title, year):
            self._merge_candidate(merged, candidate)

        if not merged:
            for candidate in await self._search_with_gigachat(title, year):
                self._merge_candidate(merged, candidate)

        if not merged and settings.has_gigachat_credentials():
            fallback_candidate = self._query_fallback_candidate(title, year)
            self._merge_candidate(merged, fallback_candidate)
            log_event(
                logger,
                logging.WARNING,
                "movie_search_query_fallback_used",
                query=query,
                title=title,
                year=year,
                fallback_title=fallback_candidate.title,
                fallback_year=fallback_candidate.year,
            )

        results = sorted(merged.values(), key=lambda candidate: candidate.confidence, reverse=True)
        final_results = results[:top_k]
        log_event(
            logger,
            logging.INFO,
            "movie_search_completed",
            query=query,
            title=title,
            year=year,
            matches=len(final_results),
            top_match=final_results[0].title if final_results else None,
        )
        return final_results

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
        if not settings.has_omdb_api_key():
            log_event(logger, logging.INFO, "provider_search_skipped", provider="omdb", reason="missing_api_key", title=title, year=year)
            return []
        if self._omdb_disabled_reason:
            log_event(
                logger,
                logging.INFO,
                "provider_search_skipped",
                provider="omdb",
                reason=self._omdb_disabled_reason,
                title=title,
                year=year,
            )
            return []

        params = {
            "apikey": settings.omdb_api_key,
            "s": title,
            "type": "movie",
        }
        if year:
            params["y"] = year

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="omdb",
            operation="search",
            method="GET",
            url="https://www.omdbapi.com/",
            params=sanitize_mapping(params),
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://www.omdbapi.com/", params=params)
                payload: dict[str, Any] = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="omdb",
                operation="search",
                method="GET",
                url="https://www.omdbapi.com/",
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            return []

        if response.status_code in {401, 403}:
            self._disable_omdb_provider("disabled_after_auth_failure")
            log_event(
                logger,
                logging.WARNING,
                "provider_auth_failed",
                provider="omdb",
                operation="search",
                method="GET",
                url="https://www.omdbapi.com/",
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                error=payload.get("Error"),
            )
            return []

        try:
            response.raise_for_status()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="omdb",
                operation="search",
                method="GET",
                url="https://www.omdbapi.com/",
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            return []

        if payload.get("Response") == "False":
            error_message = str(payload.get("Error", ""))
            if self._is_omdb_auth_error(error_message):
                self._disable_omdb_provider("disabled_after_auth_failure")
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_auth_failed",
                    provider="omdb",
                    operation="search",
                    method="GET",
                    url="https://www.omdbapi.com/",
                    status_code=response.status_code,
                    elapsed_ms=elapsed_ms(started_at),
                    error=error_message,
                )
                return []
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="omdb",
                operation="search",
                method="GET",
                url="https://www.omdbapi.com/",
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                results=0,
                response_flag=payload.get("Response"),
                error=error_message,
            )
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
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="omdb",
            operation="search",
            method="GET",
            url="https://www.omdbapi.com/",
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            results=len(results),
        )
        return results

    async def _search_tmdb(self, title: str, year: int | None) -> list[MovieCandidate]:
        if not settings.has_tmdb_api_token():
            log_event(logger, logging.INFO, "provider_search_skipped", provider="tmdb", reason="missing_api_token", title=title, year=year)
            return []

        headers = {"Authorization": f"Bearer {settings.tmdb_api_token}"}
        params: dict[str, Any] = {"query": title, "include_adult": False}
        if year:
            params["year"] = year

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="tmdb",
            operation="search",
            method="GET",
            url="https://api.themoviedb.org/3/search/movie",
            params=sanitize_mapping(params),
        )
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                response = await client.get("https://api.themoviedb.org/3/search/movie", params=params)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="tmdb",
                operation="search",
                method="GET",
                url="https://api.themoviedb.org/3/search/movie",
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
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
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="tmdb",
            operation="search",
            method="GET",
            url="https://api.themoviedb.org/3/search/movie",
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            results=len(results),
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

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="wikipedia",
            operation="title_search",
            method="GET",
            url=self.WIKIPEDIA_API_URL,
            params=sanitize_mapping(params),
        )
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self._wikipedia_headers()) as client:
                response = await client.get(self.WIKIPEDIA_API_URL, params=params)
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="title_search",
                method="GET",
                url=self.WIKIPEDIA_API_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            return await self._search_wikipedia_titles_via_summary_candidates(title, year)

        if response.status_code == 403:
            log_event(
                logger,
                logging.WARNING,
                "provider_request_forbidden",
                provider="wikipedia",
                operation="title_search",
                method="GET",
                url=self.WIKIPEDIA_API_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            return await self._search_wikipedia_titles_via_summary_candidates(title, year)

        try:
            response.raise_for_status()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="title_search",
                method="GET",
                url=self.WIKIPEDIA_API_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            return await self._search_wikipedia_titles_via_summary_candidates(title, year)

        try:
            payload = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_response_parse_failed",
                provider="wikipedia",
                operation="title_search",
                method="GET",
                url=self.WIKIPEDIA_API_URL,
                params=sanitize_mapping(params),
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            return await self._search_wikipedia_titles_via_summary_candidates(title, year)

        if len(payload) < 2 or not payload[1]:
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="wikipedia",
                operation="title_search",
                method="GET",
                url=self.WIKIPEDIA_API_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                results=0,
            )
            return await self._search_wikipedia_titles_via_summary_candidates(title, year)

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
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="wikipedia",
            operation="title_search",
            method="GET",
            url=self.WIKIPEDIA_API_URL,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            results=len(results),
        )
        return results

    async def _search_wikidata_titles(self, title: str, year: int | None) -> list[MovieCandidate]:
        params = {
            "action": "wbsearchentities",
            "search": title,
            "language": "en",
            "format": "json",
            "type": "item",
            "limit": 5,
        }

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="wikidata",
            operation="title_search",
            method="GET",
            url=self.WIKIDATA_API_URL,
            params=sanitize_mapping(params),
        )
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self._wikipedia_headers()) as client:
                response = await client.get(self.WIKIDATA_API_URL, params=params)
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikidata",
                operation="title_search",
                method="GET",
                url=self.WIKIDATA_API_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            return []

        if response.status_code == 403:
            log_event(
                logger,
                logging.WARNING,
                "provider_request_forbidden",
                provider="wikidata",
                operation="title_search",
                method="GET",
                url=self.WIKIDATA_API_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            return []

        try:
            response.raise_for_status()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikidata",
                operation="title_search",
                method="GET",
                url=self.WIKIDATA_API_URL,
                params=sanitize_mapping(params),
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            return []

        try:
            payload = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_response_parse_failed",
                provider="wikidata",
                operation="title_search",
                method="GET",
                url=self.WIKIDATA_API_URL,
                params=sanitize_mapping(params),
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            return []

        results: list[MovieCandidate] = []
        for item in payload.get("search", [])[:5]:
            description = str(item.get("description", "")).strip()
            normalized_description = description.lower()
            if not any(
                keyword in normalized_description
                for keyword in ("film", "movie", "animated film", "documentary", "television film")
            ):
                continue

            movie_id = str(item.get("id", "")).strip()
            result_title = str(item.get("label", "")).strip()
            result_year = self._parse_year(description)
            if not movie_id or not result_title:
                continue
            results.append(
                self._candidate_from_result(
                    movie_id=f"wikidata:{movie_id}",
                    title=result_title,
                    result_year=result_year,
                    query_title=title,
                    query_year=year,
                    source_bonus=0.07,
                )
            )

        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="wikidata",
            operation="title_search",
            method="GET",
            url=self.WIKIDATA_API_URL,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            results=len(results),
        )
        return results

    async def _search_wikipedia_titles_via_summary_candidates(self, title: str, year: int | None) -> list[MovieCandidate]:
        results: list[MovieCandidate] = []
        seen_titles: set[str] = set()

        for candidate_title in self._wikipedia_title_candidates(title, year):
            payload = await self._fetch_wikipedia_summary(candidate_title)
            if not payload:
                continue

            description = str(payload.get("description", "")).lower()
            if "film" not in description and "movie" not in description:
                continue

            canonical_title = (
                str(payload.get("titles", {}).get("normalized", "")).strip()
                or str(payload.get("title", "")).strip()
                or candidate_title
            )
            if canonical_title in seen_titles:
                continue
            seen_titles.add(canonical_title)

            result_year = self._parse_year(description) or self._parse_year(canonical_title)
            cleaned_title = re.sub(r"\s*\(\d{4}\s+film\)\s*$", "", canonical_title).strip()
            cleaned_title = re.sub(r"\s*\(film\)\s*$", "", cleaned_title).strip()
            if not cleaned_title:
                continue

            results.append(
                self._candidate_from_result(
                    movie_id=f"wiki:{canonical_title}",
                    title=cleaned_title,
                    result_year=result_year,
                    query_title=title,
                    query_year=year,
                    source_bonus=0.05,
                )
            )

        log_event(
            logger,
            logging.INFO,
            "provider_fallback_completed",
            provider="wikipedia",
            operation="title_search_via_summary",
            title=title,
            year=year,
            results=len(results),
        )
        return results[:5]

    async def _fetch_wikipedia_summary(self, title: str) -> dict[str, Any] | None:
        safe_title = title.replace(" ", "_")
        url = f"{self.WIKIPEDIA_SUMMARY_URL}/{safe_title}"
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self._wikipedia_headers()) as client:
                response = await client.get(url)
        except Exception:
            return None

        if response.status_code >= 400:
            return None

        try:
            payload = response.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _wikipedia_headers(cls) -> dict[str, str]:
        return {
            "User-Agent": cls.USER_AGENT,
            "Api-User-Agent": cls.USER_AGENT,
            "Accept": "application/json",
        }

    @staticmethod
    def _wikipedia_title_candidates(title: str, year: int | None) -> list[str]:
        candidates: list[str] = []
        for candidate in (
            f"{title} ({year} film)" if year else None,
            f"{title} (film)",
            title,
        ):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _disable_omdb_provider(self, reason: str) -> None:
        self._omdb_disabled_reason = reason

    async def _search_with_gigachat(self, title: str, year: int | None) -> list[MovieCandidate]:
        if not settings.has_gigachat_credentials():
            log_event(
                logger,
                logging.INFO,
                "provider_search_skipped",
                provider="gigachat",
                reason="missing_credentials",
                title=title,
                year=year,
            )
            return []

        try:
            return await asyncio.to_thread(self._search_with_gigachat_sync, title, year)
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="gigachat",
                operation="title_search_fallback",
                method="AI",
                title=title,
                year=year,
            )
            return []

    def _search_with_gigachat_sync(self, title: str, year: int | None) -> list[MovieCandidate]:
        from gigachat import GigaChat
        from gigachat.models import Chat, Messages

        movie_label = f"{title} ({year})" if year else title
        system_prompt = (
            "You identify movie titles from user queries. "
            "Use broad, widely known film knowledge. "
            "Return strict JSON with a single top-level key `candidates`, containing up to 3 objects. "
            "Each candidate object must have: title, year, confidence, reason. "
            "If the query most likely refers to a film, include the most likely film matches. "
            "If it does not look like a movie title at all, return an empty candidates array. "
            "Output JSON only."
        )
        user_prompt = (
            f"User movie query: {movie_label}\n"
            "Prefer feature films over books, TV episodes, or songs when there is a clear movie interpretation.\n"
            "Keep `reason` short and practical."
        )
        payload = Chat(
            model=settings.gigachat_model,
            temperature=0.1,
            max_tokens=500,
            messages=[
                Messages(role="system", content=system_prompt),
                Messages(role="user", content=user_prompt),
            ],
        )

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="gigachat",
            operation="title_search_fallback",
            method="AI",
            title=title,
            year=year,
            model=settings.gigachat_model,
            max_tokens=payload.max_tokens,
        )
        with GigaChat(
            credentials=settings.gigachat_credentials,
            scope=settings.gigachat_scope,
            model=settings.gigachat_model,
            base_url=settings.gigachat_base_url,
            auth_url=settings.gigachat_auth_url,
            verify_ssl_certs=settings.gigachat_verify_ssl_certs,
            ca_bundle_file=settings.gigachat_ca_bundle_file,
        ) as client:
            response = client.chat(payload)
            balance = None
            if settings.gigachat_log_balance:
                try:
                    balance = client.get_balance()
                except Exception:
                    log_exception(
                        logger,
                        "ai_balance_fetch_failed",
                        provider="gigachat",
                        operation="title_search_fallback",
                        title=title,
                        year=year,
                    )

        content = response.choices[0].message.content
        usage = response.usage
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="gigachat",
            operation="title_search_fallback",
            method="AI",
            title=title,
            year=year,
            model=response.model,
            elapsed_ms=elapsed_ms(started_at),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            precached_prompt_tokens=getattr(usage, "precached_prompt_tokens", None),
            remaining_balance=balance_snapshot(balance) if balance is not None else None,
        )
        payload_dict = self._extract_json_object(content)

        results: list[MovieCandidate] = []
        for item in payload_dict.get("candidates", [])[:3]:
            if not isinstance(item, dict):
                continue
            result_title = str(item.get("title", "")).strip()
            result_year = self._parse_year(item.get("year"))
            if not result_title:
                continue
            try:
                raw_confidence = float(item.get("confidence", 0.72))
            except (TypeError, ValueError):
                raw_confidence = 0.72
            confidence = min(max(raw_confidence, 0.55), 0.94)
            results.append(
                MovieCandidate(
                    movie_id=self._llm_candidate_id(result_title, result_year),
                    title=result_title,
                    year=result_year,
                    confidence=round(confidence, 3),
                )
            )
        return results

    @staticmethod
    def _is_omdb_auth_error(error_message: str) -> bool:
        normalized = error_message.strip().lower()
        return "invalid api key" in normalized or "api key" in normalized and "invalid" in normalized

    def _query_fallback_candidate(self, title: str, year: int | None) -> MovieCandidate:
        resolved_title = title.strip() or "Unknown movie"
        return MovieCandidate(
            movie_id=f"query:{self.normalize_title(resolved_title).replace(' ', '-') or 'unknown'}:{year or 'unknown'}",
            title=resolved_title,
            year=year,
            confidence=0.58,
        )

    def _llm_candidate_id(self, title: str, year: int | None) -> str:
        normalized = self.normalize_title(title).replace(" ", "-") or "unknown"
        return f"llm:{normalized}:{year or 'unknown'}"

    def _extract_json_object(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(
                f"GigaChat movie-search response does not contain JSON: {shorten(stripped, width=160, placeholder='...')}"
            )

        payload = json.loads(stripped[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("GigaChat movie-search JSON payload must be an object")
        return payload

    @staticmethod
    def _parse_year(value: Any) -> int | None:
        if not value:
            return None
        match = re.search(r"(19|20)\d{2}", str(value))
        return int(match.group(0)) if match else None
