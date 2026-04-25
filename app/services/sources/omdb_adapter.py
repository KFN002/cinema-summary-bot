from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.models.schemas import EvidenceChunk
from app.observability import elapsed_ms, log_event, log_exception, sanitize_mapping

logger = logging.getLogger(__name__)


class OMDbSourceAdapter:
    name = "omdb"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._disabled_reason: str | None = None

    async def fetch_movie_evidence(self, title: str, year: int | None = None) -> list[EvidenceChunk]:
        if not self.api_key or self.api_key.strip().startswith("PASTE_"):
            log_event(logger, logging.INFO, "provider_evidence_skipped", provider="omdb", reason="missing_api_key", title=title, year=year)
            return []
        if self._disabled_reason:
            log_event(logger, logging.INFO, "provider_evidence_skipped", provider="omdb", reason=self._disabled_reason, title=title, year=year)
            return []

        params = {"apikey": self.api_key, "t": title, "plot": "full"}
        if year:
            params["y"] = year

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "provider_request_started",
            provider="omdb",
            operation="evidence",
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
                operation="evidence",
                method="GET",
                url="https://www.omdbapi.com/",
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        if response.status_code in {401, 403}:
            self._disable_provider("disabled_after_auth_failure")
            log_event(
                logger,
                logging.WARNING,
                "provider_auth_failed",
                provider="omdb",
                operation="evidence",
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
                operation="evidence",
                method="GET",
                url="https://www.omdbapi.com/",
                params=sanitize_mapping(params),
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        if payload.get("Response") == "False":
            error_message = str(payload.get("Error", ""))
            if self._is_auth_error(error_message):
                self._disable_provider("disabled_after_auth_failure")
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_auth_failed",
                    provider="omdb",
                    operation="evidence",
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
                operation="evidence",
                method="GET",
                url="https://www.omdbapi.com/",
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                evidence_chunks=0,
                response_flag=payload.get("Response"),
                error=error_message,
            )
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
            log_event(
                logger,
                logging.INFO,
                "provider_request_completed",
                provider="omdb",
                operation="evidence",
                method="GET",
                url="https://www.omdbapi.com/",
                status_code=response.status_code,
                elapsed_ms=elapsed_ms(started_at),
                evidence_chunks=0,
            )
            return []

        lowered = evidence_text.lower()
        spoiler = any(marker in lowered for marker in ("ending", "dies", "twist", "killer", "identity"))
        chunks = [
            EvidenceChunk(
                source_name="OMDb",
                source_url=f"https://www.omdbapi.com/?i={payload.get('imdbID','')}",
                text=evidence_text,
                spoiler=spoiler,
            )
        ]
        log_event(
            logger,
            logging.INFO,
            "provider_request_completed",
            provider="omdb",
            operation="evidence",
            method="GET",
            url="https://www.omdbapi.com/",
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
            evidence_chunks=len(chunks),
            spoiler_chunks=sum(1 for chunk in chunks if chunk.spoiler),
        )
        return chunks

    def _disable_provider(self, reason: str) -> None:
        self._disabled_reason = reason

    @staticmethod
    def _is_auth_error(error_message: str) -> bool:
        normalized = error_message.strip().lower()
        return "invalid api key" in normalized or "api key" in normalized and "invalid" in normalized
