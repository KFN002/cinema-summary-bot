from __future__ import annotations

from typing import Any

import httpx

from app.models.schemas import EvidenceChunk


class OMDbSourceAdapter:
    name = "omdb"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        if not self.api_key:
            return []

        params = {"apikey": self.api_key, "t": title, "plot": "full"}
        if year:
            params["y"] = year

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("https://www.omdbapi.com/", params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        if payload.get("Response") == "False":
            return []

        plot = str(payload.get("Plot", "")).strip()
        if not plot or plot == "N/A":
            return []

        spoiler = "ending" in plot.lower() or "dies" in plot.lower() or "twist" in plot.lower()
        return [
            EvidenceChunk(
                source_name="OMDb",
                source_url=f"https://www.omdbapi.com/?i={payload.get('imdbID','')}",
                text=plot,
                spoiler=spoiler,
            )
        ]
