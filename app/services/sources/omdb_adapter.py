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
            plot = ""

        details = [
            f"Plot: {plot}" if plot else "",
            f"Genre: {payload.get('Genre')}" if payload.get("Genre") and payload.get("Genre") != "N/A" else "",
            f"Director: {payload.get('Director')}" if payload.get("Director") and payload.get("Director") != "N/A" else "",
            f"Actors: {payload.get('Actors')}" if payload.get("Actors") and payload.get("Actors") != "N/A" else "",
            f"Awards: {payload.get('Awards')}" if payload.get("Awards") and payload.get("Awards") != "N/A" else "",
        ]
        evidence_text = "\n".join(part for part in details if part).strip()
        if not evidence_text:
            return []

        lowered = evidence_text.lower()
        spoiler = any(marker in lowered for marker in ("ending", "dies", "twist", "killer", "identity"))
        return [
            EvidenceChunk(
                source_name="OMDb",
                source_url=f"https://www.omdbapi.com/?i={payload.get('imdbID','')}",
                text=evidence_text,
                spoiler=spoiler,
            )
        ]
