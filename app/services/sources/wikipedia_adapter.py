from __future__ import annotations

import re
from typing import Any

import httpx

from app.models.schemas import EvidenceChunk


class WikipediaSourceAdapter:
    name = "wikipedia"
    BASE_URL = "https://en.wikipedia.org/w/api.php"

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        article_title = await self._resolve_title(title, year)
        if not article_title:
            return []

        extract = await self._fetch_extract(article_title)
        if not extract:
            return []

        plot_chunk, spoiler_chunk = self._split_spoilers(extract)
        chunks = [
            EvidenceChunk(
                source_name="Wikipedia",
                source_url=f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}",
                text=plot_chunk,
                spoiler=False,
            )
        ]
        if spoiler_chunk:
            chunks.append(
                EvidenceChunk(
                    source_name="Wikipedia",
                    source_url=f"https://en.wikipedia.org/wiki/{article_title.replace(' ', '_')}",
                    text=spoiler_chunk,
                    spoiler=True,
                )
            )
        return chunks

    async def _resolve_title(self, title: str, year: int | None) -> str | None:
        search_phrase = f"{title} {year or ''} film".strip()
        params = {
            "action": "opensearch",
            "search": search_phrase,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        if len(payload) < 2 or not payload[1]:
            return None
        return str(payload[1][0])

    async def _fetch_extract(self, title: str) -> str | None:
        params = {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "plain",
            "titles": title,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        pages = payload.get("query", {}).get("pages", {})
        if not pages:
            return None
        page = next(iter(pages.values()))
        extract = page.get("extract", "")
        return str(extract) if extract else None

    @staticmethod
    def _split_spoilers(extract: str) -> tuple[str, str]:
        lowered = extract.lower()
        markers = ["plot", "ending", "final", "twist"]
        spoiler_index = min((lowered.find(marker) for marker in markers if lowered.find(marker) != -1), default=-1)
        clean = re.sub(r"\n{3,}", "\n\n", extract).strip()
        if spoiler_index <= 0:
            return clean[:3200], ""
        return clean[:spoiler_index][:2400], clean[spoiler_index:][:1800]
