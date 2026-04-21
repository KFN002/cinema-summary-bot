from __future__ import annotations

from textwrap import shorten

from app.models.schemas import EvidenceChunk, MovieExplanation


class GroundedSummarizer:
    """MVP summarizer: deterministic evidence-to-schema transform.

    Replace with OpenAI structured outputs in production.
    """

    def summarize(
        self,
        title: str,
        year: int | None,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
    ) -> MovieExplanation:
        non_spoiler_text = " ".join(chunk.text for chunk in evidence if not chunk.spoiler)
        spoiler_text = " ".join(chunk.text for chunk in evidence if chunk.spoiler)

        summary = shorten(non_spoiler_text, width=520, placeholder="…") or "Insufficient evidence."
        hidden_details = "Key details likely include visual motifs, callbacks, and dialogue hints in the source text."
        interpretations = "Insufficient evidence for deep interpretation." if not non_spoiler_text else (
            "The narrative can be interpreted through character psychology and thematic ambiguity."
        )

        if allow_spoilers and spoiler_text:
            ending = shorten(spoiler_text, width=520, placeholder="…")
            spoiler_level = "full"
        else:
            ending = "Spoiler section hidden. Tap 'Show spoilers' to reveal the ending explanation."
            spoiler_level = "none"

        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary=summary,
            ending_explained=ending,
            hidden_details=hidden_details,
            interpretations=interpretations,
            spoiler_level=spoiler_level,
            evidence=evidence,
            from_cache=False,
        )
