from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ResponseMode(str, Enum):
    no_spoilers = "no_spoilers"
    ending_explained = "ending_explained"
    hidden_details = "hidden_details"
    interpretations = "interpretations"


class MovieCandidate(BaseModel):
    movie_id: str
    title: str
    year: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class UserQuery(BaseModel):
    title: str
    mode: Literal[
        "no_spoilers",
        "ending_explained",
        "hidden_details",
        "interpretations",
    ] = "no_spoilers"
    allow_spoilers: bool = False


class EvidenceChunk(BaseModel):
    source_name: str
    source_url: str
    text: str
    spoiler: bool = False


class MovieExplanation(BaseModel):
    canonical_title: str
    year: int | None = None
    summary: str
    ending_explained: str
    hidden_details: str
    interpretations: str
    spoiler_level: Literal["none", "light", "full"]
    evidence: list[EvidenceChunk]
    from_cache: bool = False


class ExplainResponse(BaseModel):
    query: UserQuery
    candidates: list[MovieCandidate] = Field(default_factory=list)
    explanation: MovieExplanation | None = None
    requires_disambiguation: bool = False
