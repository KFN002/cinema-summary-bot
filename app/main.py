from __future__ import annotations

from fastapi import FastAPI

from app.bootstrap import build_pipeline
from app.models.schemas import ExplainResponse, UserQuery

app = FastAPI(title="Cinema Summary Bot MVP", version="0.2.0")
pipeline = build_pipeline()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/explain", response_model=ExplainResponse)
async def explain(query: UserQuery) -> ExplainResponse:
    return await pipeline.run(query)
