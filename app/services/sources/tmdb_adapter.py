from __future__ import annotations

from typing import Any

import httpx

from app.models.schemas import EvidenceChunk


class TMDbSourceAdapter:
    name = "tmdb"

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        if not self.api_token:
            return []

        headers = {"Authorization": f"Bearer {self.api_token}"}
        query_params: dict[str, Any] = {"query": title, "include_adult": False}
        if year:
            query_params["year"] = year

        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            search_response = await client.get("https://api.themoviedb.org/3/search/movie", params=query_params)
            search_response.raise_for_status()
            search_payload: dict[str, Any] = search_response.json()
            results = search_payload.get("results", [])
            if not results:
                return []

            movie_id = results[0]["id"]
            movie_response = await client.get(f"https://api.themoviedb.org/3/movie/{movie_id}")
            movie_response.raise_for_status()
            movie_payload: dict[str, Any] = movie_response.json()

        overview = str(movie_payload.get("overview", "")).strip()
        if not overview:
            return []

        return [
            EvidenceChunk(
                source_name="TMDb",
                source_url=f"https://www.themoviedb.org/movie/{movie_id}",
                text=overview,
                spoiler=False,
            )
        ]
