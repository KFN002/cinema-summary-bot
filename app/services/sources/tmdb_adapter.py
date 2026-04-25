from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.models.schemas import EvidenceChunk
from app.observability import elapsed_ms, log_event, log_exception, sanitize_mapping

logger = logging.getLogger(__name__)


class TMDbSourceAdapter:
    name = "tmdb"

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        if not self.api_token or self.api_token.strip().startswith("PASTE_"):
            log_event(logger, logging.INFO, "provider_evidence_skipped", provider="tmdb", reason="missing_api_token", title=title, year=year)
            return []

        headers = {"Authorization": f"Bearer {self.api_token}"}
        query_params: dict[str, Any] = {"query": title, "include_adult": False}
        if year:
            query_params["year"] = year

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="tmdb",
            operation="evidence_search",
            method="GET",
            url="https://api.themoviedb.org/3/search/movie",
            params=sanitize_mapping(query_params),
        )
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                search_response = await client.get("https://api.themoviedb.org/3/search/movie", params=query_params)
                search_response.raise_for_status()
                search_payload: dict[str, Any] = search_response.json()
                results = search_payload.get("results", [])
                if not results:
                    log_event(
                        logger,
                        logging.INFO,
                        "provider_request_completed",
                        provider="tmdb",
                        operation="evidence_search",
                        method="GET",
                        url="https://api.themoviedb.org/3/search/movie",
                        status_code=search_response.status_code,
                        elapsed_ms=elapsed_ms(started_at),
                        results=0,
                    )
                    return []

                movie_id = results[0]["id"]
                log_event(
                    logger,
                    logging.INFO,
                    "provider_request_started",
                    provider="tmdb",
                    operation="evidence_details",
                    method="GET",
                    url=f"https://api.themoviedb.org/3/movie/{movie_id}",
                    params={"append_to_response": "credits"},
                )
                movie_response = await client.get(
                    f"https://api.themoviedb.org/3/movie/{movie_id}",
                    params={"append_to_response": "credits"},
                )
                movie_response.raise_for_status()
                movie_payload: dict[str, Any] = movie_response.json()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="tmdb",
                operation="evidence",
                method="GET",
                url="https://api.themoviedb.org/3/search/movie",
                params=sanitize_mapping(query_params),
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        overview = str(movie_payload.get("overview", "")).strip()
        tagline = str(movie_payload.get("tagline", "")).strip()
        genres = ", ".join(genre["name"] for genre in movie_payload.get("genres", []) if genre.get("name"))
        cast = ", ".join(
            member.get("name", "")
            for member in movie_payload.get("credits", {}).get("cast", [])[:5]
            if member.get("name")
        )
        details = [
            f"Overview: {overview}" if overview else "",
            f"Tagline: {tagline}" if tagline else "",
            f"Genres: {genres}" if genres else "",
            f"Top cast: {cast}" if cast else "",
        ]
        evidence_text = "\n".join(part for part in details if part).strip()
        if not evidence_text:
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="tmdb",
                operation="evidence",
                method="GET",
                url=f"https://api.themoviedb.org/3/movie/{movie_id}",
                status_code=movie_response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                evidence_chunks=0,
            )
            return []

        chunks = [
            EvidenceChunk(
                source_name="TMDb",
                source_url=f"https://www.themoviedb.org/movie/{movie_id}",
                text=evidence_text,
                spoiler=False,
            )
        ]
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="tmdb",
            operation="evidence",
            method="GET",
            url=f"https://api.themoviedb.org/3/movie/{movie_id}",
            status_code=movie_response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            evidence_chunks=len(chunks),
        )
        return chunks
