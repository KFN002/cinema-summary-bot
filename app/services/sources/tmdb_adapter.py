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
            movie_response = await client.get(
                f"https://api.themoviedb.org/3/movie/{movie_id}",
                params={"append_to_response": "credits"},
            )
            movie_response.raise_for_status()
            movie_payload: dict[str, Any] = movie_response.json()

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
            return []

        return [
            EvidenceChunk(
                source_name="TMDb",
                source_url=f"https://www.themoviedb.org/movie/{movie_id}",
                text=evidence_text,
                spoiler=False,
            )
        ]
