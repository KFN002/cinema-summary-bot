import pytest

from app.models.schemas import EvidenceChunk
from app.models.schemas import MovieExplanation
from app.services.llm.summarizer import GroundedSummarizer


def test_watched_summary_uses_concrete_spoiler_details():
    summarizer = GroundedSummarizer()
    explanation = summarizer._summarize_small(
        title="Shutter Island",
        year=2010,
        evidence=[
            EvidenceChunk(
                source_name="Wikipedia",
                source_url="https://example.com/wiki",
                text=(
                    "U.S. Marshal Teddy Daniels arrives on Shutter Island with Chuck Aule to investigate the disappearance of Rachel Solando. "
                    "The case pushes Teddy through interviews, missing clues, and flashbacks tied to his wife Dolores."
                ),
                spoiler=False,
            ),
            EvidenceChunk(
                source_name="Wikipedia",
                source_url="https://example.com/wiki",
                text=(
                    "It is revealed that Teddy is actually Andrew Laeddis, a patient at Ashecliffe, and Chuck is really his psychiatrist Dr. Sheehan. "
                    "Andrew killed his manic-depressive wife after she drowned their children. "
                    "In the final scene he chooses the lobotomy rather than live with that knowledge."
                ),
                spoiler=True,
            ),
        ],
        allow_spoilers=True,
        watched=True,
    )

    assert "Andrew Laeddis" in explanation.summary
    assert "psychiatrist" in explanation.summary or "Dr. Sheehan" in explanation.summary
    assert "lobotomy" in explanation.ending_explained


def test_watched_hidden_details_prefers_clue_language_over_generic_template():
    summarizer = GroundedSummarizer()
    explanation = summarizer._summarize_small(
        title="Shutter Island",
        year=2010,
        evidence=[
            EvidenceChunk(
                source_name="Wikipedia",
                source_url="https://example.com/wiki",
                text=(
                    "The investigation keeps pointing back to Andrew's identity, the doctors around him, and the constructed therapeutic role-play inside the hospital."
                ),
                spoiler=True,
            ),
            EvidenceChunk(
                source_name="TMDb",
                source_url="https://example.com/tmdb",
                text="Top cast: Leonardo DiCaprio, Mark Ruffalo. Director: Martin Scorsese.",
                spoiler=False,
            ),
        ],
        allow_spoilers=True,
        watched=True,
    )

    assert "identity" in explanation.hidden_details.lower() or "doctors" in explanation.hidden_details.lower()


@pytest.mark.asyncio
async def test_empty_evidence_uses_gigachat_standard_fallback(monkeypatch):
    summarizer = GroundedSummarizer()
    monkeypatch.setattr(summarizer, "_can_use_gigachat", lambda: True)

    def fake_compose(base_explanation, title, year, evidence, allow_spoilers, watched):
        assert evidence == []
        assert allow_spoilers is False
        assert watched is False
        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary="A specific spoiler-safe summary from GigaChat.",
            ending_explained="A spoiler-safe payoff description from GigaChat.",
            hidden_details="Concrete themes and craft details from GigaChat.",
            interpretations="A couple of grounded interpretations from GigaChat.",
            spoiler_level="none",
            evidence=[],
        )

    monkeypatch.setattr(summarizer, "_compose_standard_with_gigachat", fake_compose)

    explanation = await summarizer.summarize(
        title="Trainspotting",
        year=1996,
        evidence=[],
        allow_spoilers=False,
        watched=False,
    )

    assert explanation.summary == "A specific spoiler-safe summary from GigaChat."
    assert explanation.spoiler_level == "none"


def test_expand_with_gigachat_coerces_structured_section_values(monkeypatch):
    summarizer = GroundedSummarizer()
    base = MovieExplanation(
        canonical_title="Trainspotting",
        year=1996,
        summary="Base summary",
        ending_explained="Base ending",
        hidden_details="Base hidden details",
        interpretations="Base interpretations",
        spoiler_level="full",
        evidence=[],
    )

    monkeypatch.setattr(
        summarizer,
        "_run_gigachat_request",
        lambda **kwargs: {
            "summary": ["Trainspotting follows Renton and his friends through heroin addiction in Edinburgh."],
            "ending_explained": {
                "title": "Ending",
                "body": "Renton betrays the group, takes the money, and chooses to start over on his own terms.",
            },
            "hidden_details": [
                {"title": "Motif", "body": "The film keeps tying euphoria to self-destruction and rot."},
                {"title": "Style", "body": "Boyle uses hyperactive editing and music to trap us inside Renton's highs and crashes."},
            ],
            "interpretations": [
                {
                    "title": "Literal vs Symbolic",
                    "body": "One reading sees the ending as a selfish but real escape; another sees it as Renton performing adulthood without fully changing.",
                }
            ],
            "spoiler_level": "full",
        },
    )

    explanation = summarizer._expand_with_gigachat(
        base,
        title="Trainspotting",
        year=1996,
        evidence=[],
        allow_spoilers=True,
        watched=True,
        focus_section="interpretations",
    )

    assert "Renton" in explanation.summary
    assert "takes the money" in explanation.ending_explained
    assert "Literal vs Symbolic" in explanation.interpretations
    assert "hyperactive editing" in explanation.hidden_details


@pytest.mark.asyncio
async def test_expanded_empty_evidence_preserves_ai_base_if_expand_fails(monkeypatch):
    summarizer = GroundedSummarizer()
    monkeypatch.setattr(summarizer, "_can_use_gigachat", lambda: True)

    def fake_standard(base_explanation, title, year, evidence, allow_spoilers, watched):
        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary="Rich AI base summary for Trainspotting.",
            ending_explained="Rich AI base ending explanation for Trainspotting.",
            hidden_details="Rich AI base hidden details for Trainspotting.",
            interpretations="Rich AI base interpretation text for Trainspotting that is already much stronger than the local fallback.",
            spoiler_level="full",
            evidence=[],
        )

    def fake_expand(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(summarizer, "_compose_standard_with_gigachat", fake_standard)
    monkeypatch.setattr(summarizer, "_expand_with_gigachat", fake_expand)

    explanation = await summarizer.summarize(
        title="Trainspotting",
        year=1996,
        evidence=[],
        allow_spoilers=True,
        watched=True,
        detail_level="expanded",
        focus_section="interpretations",
    )

    assert explanation.interpretations.startswith("Rich AI base interpretation text")
