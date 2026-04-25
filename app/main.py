from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request

from app.bootstrap import build_pipeline
from app.config import settings
from app.models.schemas import ExplainResponse, UserQuery
from app.observability import bind_log_context, configure_logging, elapsed_ms, log_event, log_exception, new_request_id

configure_logging(settings.log_level)

logger = logging.getLogger(__name__)

app = FastAPI(title="Cinema Summary Bot MVP", version="0.2.0")
pipeline = build_pipeline()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or new_request_id("api")
    started_at = time.perf_counter()
    with bind_log_context(request_id=request_id, channel="api"):
        log_event(
            logger,
            logging.INFO,
            "api_request_started",
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            client=getattr(request.client, "host", None),
        )
        try:
            response = await call_next(request)
        except Exception:
            log_exception(
                logger,
                "api_request_failed",
                method=request.method,
                path=request.url.path,
                elapsed_ms=elapsed_ms(started_at),
            )
            raise

        response.headers["x-request-id"] = request_id
        log_event(
            logger,
            logging.INFO,
            "api_request_finished",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms(started_at),
        )
        return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/explain", response_model=ExplainResponse)
async def explain(query: UserQuery) -> ExplainResponse:
    return await pipeline.run(query)
