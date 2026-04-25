from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from app.models.schemas import EvidenceChunk
from app.observability import elapsed_ms, log_event, log_exception, sanitize_mapping

logger = logging.getLogger(__name__)


class WikipediaSourceAdapter:
    name = "wikipedia"
    BASE_URL = "https://en.wikipedia.org/w/api.php"
    SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
    USER_AGENT = "cinema-summary-bot/0.2 (movie evidence fetcher)"

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        log_event(logger, logging.INFO, "provider_evidence_started", provider="wikipedia", title=title, year=year)
        article_title = await self._resolve_title(title, year)
        if not article_title:
            log_event(logger, logging.INFO, "provider_evidence_completed", provider="wikipedia", title=title, year=year, article_title=None, evidence_chunks=0)
            return []

        extract = await self._fetch_extract(article_title)
        if not extract:
            log_event(logger, logging.INFO, "provider_evidence_completed", provider="wikipedia", title=title, year=year, article_title=article_title, evidence_chunks=0)
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
        log_event(
            logger,
            logging.INFO,
            "provider_evidence_completed",
            provider="wikipedia",
            title=title,
            year=year,
            article_title=article_title,
            evidence_chunks=len(chunks),
            spoiler_chunks=sum(1 for chunk in chunks if chunk.spoiler),
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
        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="wikipedia",
            operation="resolve_title",
            method="GET",
            url=self.BASE_URL,
            params=sanitize_mapping(params),
        )
        try:
            async with httpx.AsyncClient(timeout=8.0, headers=self._headers()) as client:
                response = await client.get(self.BASE_URL, params=params)
                payload = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="resolve_title",
                method="GET",
                url=self.BASE_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            candidates = self._title_candidates(title, year)
            for candidate in candidates:
                if await self._fetch_summary_extract(candidate):
                    log_event(
                        logger,
                        logging.INFO,
                        "provider_resolve_title_fallback_hit",
                        provider="wikipedia",
                        title=title,
                        year=year,
                        article_title=candidate,
                    )
                    return candidate
            return None

        if response.status_code == 403:
            log_event(
                logger,
                logging.WARNING,
                "provider_request_forbidden",
                provider="wikipedia",
                operation="resolve_title",
                method="GET",
                url=self.BASE_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            candidates = self._title_candidates(title, year)
            for candidate in candidates:
                if await self._fetch_summary_extract(candidate):
                    log_event(
                        logger,
                        logging.INFO,
                        "provider_resolve_title_fallback_hit",
                        provider="wikipedia",
                        title=title,
                        year=year,
                        article_title=candidate,
                    )
                    return candidate
            return None

        try:
            response.raise_for_status()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="resolve_title",
                method="GET",
                url=self.BASE_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            candidates = self._title_candidates(title, year)
            for candidate in candidates:
                if await self._fetch_summary_extract(candidate):
                    log_event(
                        logger,
                        logging.INFO,
                        "provider_resolve_title_fallback_hit",
                        provider="wikipedia",
                        title=title,
                        year=year,
                        article_title=candidate,
                    )
                    return candidate
            return None

        if len(payload) < 2 or not payload[1]:
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="wikipedia",
                operation="resolve_title",
                method="GET",
                url=self.BASE_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                results=0,
            )
            candidates = self._title_candidates(title, year)
            for candidate in candidates:
                if await self._fetch_summary_extract(candidate):
                    log_event(
                        logger,
                        logging.INFO,
                        "provider_resolve_title_fallback_hit",
                        provider="wikipedia",
                        title=title,
                        year=year,
                        article_title=candidate,
                    )
                    return candidate
            return None
        article_title = str(payload[1][0])
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="wikipedia",
            operation="resolve_title",
            method="GET",
            url=self.BASE_URL,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            results=len(payload[1]),
            article_title=article_title,
        )
        return article_title

    async def _fetch_extract(self, title: str) -> str | None:
        params = {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "plain",
            "titles": title,
            "format": "json",
        }
        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="wikipedia",
            operation="fetch_extract",
            method="GET",
            url=self.BASE_URL,
            params=sanitize_mapping(params),
        )
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=self._headers()) as client:
                response = await client.get(self.BASE_URL, params=params)
                payload: dict[str, Any] = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="fetch_extract",
                method="GET",
                url=self.BASE_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        if response.status_code == 403:
            log_event(
                logger,
                logging.WARNING,
                "provider_request_forbidden",
                provider="wikipedia",
                operation="fetch_extract",
                method="GET",
                url=self.BASE_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
            )
            return await self._fetch_summary_extract(title)

        try:
            response.raise_for_status()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="fetch_extract",
                method="GET",
                url=self.BASE_URL,
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        pages = payload.get("query", {}).get("pages", {})
        if not pages:
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="wikipedia",
                operation="fetch_extract",
                method="GET",
                url=self.BASE_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                extract_found=False,
            )
            return await self._fetch_summary_extract(title)
        page = next(iter(pages.values()))
        extract = page.get("extract", "")
        if extract:
            cleaned_extract = str(extract)
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="wikipedia",
                operation="fetch_extract",
                method="GET",
                url=self.BASE_URL,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                extract_found=True,
                extract_chars=len(cleaned_extract),
            )
            return cleaned_extract
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="wikipedia",
            operation="fetch_extract",
            method="GET",
            url=self.BASE_URL,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            extract_found=False,
        )
        return await self._fetch_summary_extract(title)

    async def _fetch_summary_extract(self, title: str) -> str | None:
        safe_title = title.replace(" ", "_")
        url = f"{self.SUMMARY_URL}/{safe_title}"
        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="wikipedia",
            operation="fetch_summary_extract",
            method="GET",
            url=url,
        )
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=self._headers()) as client:
                response = await client.get(url)
                if response.status_code >= 400:
                    log_event(
                        logger,
                        logging.INFO,
                        "provider_request_completed",
                        provider="wikipedia",
                        operation="fetch_summary_extract",
                        method="GET",
                        url=url,
                        status_code=response.status_code,
                        elapsed_ms=elapsed_ms(started_at),
                        extract_found=False,
                    )
                    return None
                payload: dict[str, Any] = response.json()
        except Exception:
            log_exception(
                logger,
                "provider_request_failed",
                provider="wikipedia",
                operation="fetch_summary_extract",
                method="GET",
                url=url,
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        extract = payload.get("extract")
        cleaned_extract = str(extract).strip() if extract else None
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="wikipedia",
            operation="fetch_summary_extract",
            method="GET",
            url=url,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            extract_found=bool(cleaned_extract),
            extract_chars=len(cleaned_extract) if cleaned_extract else 0,
        )
        return cleaned_extract

    @classmethod
    def _headers(cls) -> dict[str, str]:
        return {
            "User-Agent": cls.USER_AGENT,
            "Api-User-Agent": cls.USER_AGENT,
            "Accept": "application/json",
        }

    @staticmethod
    def _title_candidates(title: str, year: int | None) -> list[str]:
        candidates = [title]
        if year:
            candidates.append(f"{title} ({year} film)")
        candidates.append(f"{title} (film)")
        return candidates

    @staticmethod
    def _split_spoilers(extract: str) -> tuple[str, str]:
        lowered = extract.lower()
        markers = ["plot", "ending", "final", "twist"]
        spoiler_index = min((lowered.find(marker) for marker in markers if lowered.find(marker) != -1), default=-1)
        clean = re.sub(r"\n{3,}", "\n\n", extract).strip()
        if spoiler_index <= 0:
            return clean[:3200], ""
        return clean[:spoiler_index][:2400], clean[spoiler_index:][:1800]
