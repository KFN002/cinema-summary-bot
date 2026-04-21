from __future__ import annotations

import asyncio
import json
import re
from textwrap import shorten

from app.models.schemas import EvidenceChunk, MovieExplanation
from app.config import settings


class GroundedSummarizer:
    """Small responses are local; expanded sections are AI-generated on demand."""

    SECTION_STYLE = {
        "summary": "What the movie is really about",
        "ending_explained": "Ending explained",
        "hidden_details": "Details you may have missed",
        "interpretations": "Two main interpretations",
    }

    async def summarize(
        self,
        title: str,
        year: int | None,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
        watched: bool,
        detail_level: str = "standard",
        focus_section: str | None = None,
    ) -> MovieExplanation:
        base_explanation = self._summarize_small(title, year, evidence, allow_spoilers, watched)
        if detail_level != "expanded" or not focus_section:
            return base_explanation

        if self._can_use_gigachat():
            try:
                return await asyncio.to_thread(
                    self._expand_with_gigachat,
                    base_explanation,
                    title,
                    year,
                    evidence,
                    allow_spoilers,
                    watched,
                    focus_section,
                )
            except Exception:
                pass

        return self._expand_fallback(base_explanation, title, evidence, allow_spoilers, watched, focus_section)

    def _can_use_gigachat(self) -> bool:
        return (
            settings.gigachat_credentials.strip()
            and settings.gigachat_credentials != "PASTE_GIGACHAT_AUTH_KEY_HERE"
        )

    def _expand_with_gigachat(
        self,
        base_explanation: MovieExplanation,
        title: str,
        year: int | None,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
        watched: bool,
        focus_section: str,
    ) -> MovieExplanation:
        from gigachat import GigaChat
        from gigachat.models import Chat, Messages

        movie_label = f"{title} ({year})" if year else title
        serialized_evidence = "\n\n".join(
            (
                f"Source: {chunk.source_name}\n"
                f"URL: {chunk.source_url}\n"
                f"Spoiler: {'yes' if chunk.spoiler else 'no'}\n"
                f"Text: {chunk.text}"
            )
            for chunk in evidence
        )
        spoiler_policy = (
            "Spoilers are allowed. You may explain the ending in detail."
            if allow_spoilers
            else "Spoilers are not allowed. Do not reveal the ending. "
            "Return a spoiler-safe placeholder in ending_explained."
        )
        audience_goal = (
            "The user has already watched the movie. Expand the requested section with spoilers, deeper reasoning, and richer discussion."
            if watched
            else "The user has not watched the movie yet. Keep the requested section spoiler-free, but make it much more vivid, persuasive, and engaging."
        )
        base_snapshot = (
            f"Current small summary:\n"
            f"- summary: {base_explanation.summary}\n"
            f"- ending_explained: {base_explanation.ending_explained}\n"
            f"- hidden_details: {base_explanation.hidden_details}\n"
            f"- interpretations: {base_explanation.interpretations}\n"
        )

        system_prompt = (
            "You are a movie explanation assistant. "
            "Prefer the supplied evidence first, but if the evidence is thin, use broad, widely known movie knowledge and common interpretations to still produce a useful answer. "
            "Do not fabricate obscure scene details or fake citations. Answer in clear natural English. "
            "Return strict JSON with keys: "
            "summary, ending_explained, hidden_details, interpretations, spoiler_level. "
            "Output JSON only, without markdown fences."
        )
        user_prompt = (
            f"Movie: {movie_label}\n"
            f"Audience mode: {'watched' if watched else 'not_watched'}\n"
            f"Detail level: expanded\n"
            f"Focus section: {focus_section}\n"
            f"{spoiler_policy}\n\n"
            f"Goal: {audience_goal}\n\n"
            "Expansion goal: Replace only the requested section with a significantly bigger version while keeping the other sections as they are.\n\n"
            f"{base_snapshot}\n"
            "Rules:\n"
            "- Return all four keys again, but only the focus section should become much bigger.\n"
            "- The focus section must be significantly larger than the small version, usually several dense paragraphs.\n"
            "- Keep the three non-focused sections close to the current small version.\n"
            "- Never answer with 'not enough evidence' or other filler if a useful movie explanation can still be produced.\n"
            "- spoiler_level must be one of: none, light, full.\n\n"
            f"Evidence:\n{serialized_evidence}"
        )
        payload = Chat(
            model=settings.gigachat_model,
            temperature=0.2,
            max_tokens=3200,
            messages=[
                Messages(role="system", content=system_prompt),
                Messages(role="user", content=user_prompt),
            ],
        )

        with GigaChat(
            credentials=settings.gigachat_credentials,
            scope=settings.gigachat_scope,
            model=settings.gigachat_model,
            base_url=settings.gigachat_base_url,
            auth_url=settings.gigachat_auth_url,
            verify_ssl_certs=settings.gigachat_verify_ssl_certs,
            ca_bundle_file=settings.gigachat_ca_bundle_file,
        ) as client:
            response = client.chat(payload)

        content = response.choices[0].message.content
        payload = self._extract_json(content)
        spoiler_level = payload.get("spoiler_level", "none")
        if spoiler_level not in {"none", "light", "full"}:
            spoiler_level = "full" if allow_spoilers else "none"

        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary=payload.get("summary") or base_explanation.summary,
            ending_explained=payload.get("ending_explained") or base_explanation.ending_explained,
            hidden_details=payload.get("hidden_details") or base_explanation.hidden_details,
            interpretations=payload.get("interpretations") or base_explanation.interpretations,
            spoiler_level=spoiler_level,
            evidence=evidence,
        )

    def _extract_json(self, content: str) -> dict[str, str]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("GigaChat response does not contain JSON")

        payload = json.loads(stripped[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("GigaChat JSON payload must be an object")
        return payload

    def _default_summary(self, title: str, watched: bool) -> str:
        if watched:
            return (
                f"{title} is structured around a reality shift that changes how the protagonist, the central conflict, "
                "and the earlier scenes are meant to be understood. The story is usually built so that the audience first accepts one framework "
                "for what is happening, then has to reinterpret character behavior, visual clues, and emotional stakes through a second framework "
                "that makes the whole movie feel different in retrospect."
            )
        return (
            f"{title} looks designed to hook viewers through atmosphere, escalating tension, and a mystery that keeps reframing what seems obvious. "
            "Even without giving away key reveals, it has the kind of setup that invites curiosity because every new beat seems to raise the stakes "
            "or make the world feel a little stranger, more unstable, or more emotionally loaded."
        )

    def _default_ending(self, title: str) -> str:
        return (
            f"The ending of {title} is usually read in more than one way: one explanation takes the final reveal at face value, "
            "while another focuses on ambiguity, psychology, or a conscious final choice. What makes the ending memorable is that it usually does not "
            "just close the plot; it also redefines the emotional meaning of everything that came before it."
        )

    def _default_hidden_details(self, title: str, watched: bool) -> str:
        if watched:
            return (
                f"In {title}, the most discussed hidden details usually come from repeated visual motifs, dialogue phrasing, performance choices, "
                "and background clues that only fully click once you know where the story is heading. The details people revisit most often are usually "
                "the ones that seem ordinary on a first watch but later look deliberate, ironic, or quietly revealing."
            )
        return (
            f"What makes {title} stand out is usually its mood, craft, and the way it quietly suggests there is more going on beneath the surface. "
            "That combination tends to make it feel less like a straightforward plot machine and more like a movie you want to keep leaning into."
        )

    def _default_interpretations(self, title: str, watched: bool) -> str:
        if watched:
            return (
                f"The main debates around {title} usually split between a literal reading of events and a more symbolic or psychological reading "
                "that changes what the ending means. The most interesting part of that debate is usually not just which reading is 'correct,' but how each one "
                "changes the movie's themes, moral weight, and the way the protagonist should be judged."
            )
        return (
            f"{title} should appeal most to viewers who like layered themes, tension, and stories that invite interpretation after the credits roll. "
            "It is the kind of movie that aims to stay in your head rather than simply deliver a plot and move on."
        )

    def _summarize_small(
        self,
        title: str,
        year: int | None,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
        watched: bool,
    ) -> MovieExplanation:
        non_spoiler_text = " ".join(chunk.text for chunk in evidence if not chunk.spoiler)
        spoiler_text = " ".join(chunk.text for chunk in evidence if chunk.spoiler)
        combined_text = " ".join(chunk.text for chunk in evidence)
        lead_non_spoiler = self._extract_sentences(non_spoiler_text or combined_text, 2)
        lead_spoiler = self._extract_sentences(spoiler_text or combined_text, 2)

        if watched:
            summary = lead_non_spoiler or self._default_summary(title, watched)
            hidden_details = self._build_hidden_details_small(title, evidence, watched)
            interpretations = (
                f"A common reading of {title} treats the plot literally, while another argues the movie is really about distorted perception, self-deception, memory, or a symbolic emotional reality."
                if combined_text
                else "The ending likely supports multiple readings, including a literal one and a more psychological or symbolic one."
            )
        else:
            summary = lead_non_spoiler or self._default_summary(title, watched)
            hidden_details = self._build_hidden_details_small(title, evidence, watched)
            interpretations = f"{title} should work best for viewers who enjoy tension, layered themes, and stories that keep raising questions." if non_spoiler_text else (
                self._default_interpretations(title, watched)
            )

        if allow_spoilers and spoiler_text:
            ending = lead_spoiler or self._default_ending(title)
            spoiler_level = "full"
        elif allow_spoilers and combined_text:
            ending = lead_spoiler or self._default_ending(title)
            spoiler_level = "light"
        else:
            ending = f"This stays spoiler-free for now, but {title} is clearly built around reveals that change how you read earlier scenes."
            spoiler_level = "none"

        if not watched:
            summary = shorten(summary, width=260, placeholder="…")
            ending = shorten(ending, width=220, placeholder="…")
            hidden_details = shorten(hidden_details, width=260, placeholder="…")
            interpretations = shorten(interpretations, width=260, placeholder="…")
        else:
            summary = shorten(summary, width=520, placeholder="…")
            ending = shorten(ending, width=520, placeholder="…")
            hidden_details = shorten(hidden_details, width=460, placeholder="…")
            interpretations = shorten(interpretations, width=460, placeholder="…")

        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary=summary,
            ending_explained=ending,
            hidden_details=hidden_details,
            interpretations=interpretations,
            spoiler_level=spoiler_level,
            evidence=evidence,
        )

    def _expand_fallback(
        self,
        base_explanation: MovieExplanation,
        title: str,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
        watched: bool,
        focus_section: str,
    ) -> MovieExplanation:
        expanded = base_explanation.model_copy(deep=True)
        combined_text = " ".join(chunk.text for chunk in evidence)
        spoiler_text = " ".join(chunk.text for chunk in evidence if chunk.spoiler)

        if focus_section == "summary":
            expanded.summary = (
                shorten(combined_text, width=2200, placeholder="…")
                if combined_text
                else self._default_summary(title, watched)
            )
        elif focus_section == "ending_explained":
            expanded.ending_explained = (
                shorten(spoiler_text or combined_text, width=2200, placeholder="…")
                if (spoiler_text or combined_text)
                else self._default_ending(title)
            )
            if allow_spoilers:
                expanded.spoiler_level = "full" if spoiler_text else "light"
        elif focus_section == "hidden_details":
            expanded.hidden_details = (
                shorten(combined_text, width=2200, placeholder="…")
                if combined_text
                else self._default_hidden_details(title, watched)
            )
        elif focus_section == "interpretations":
            expanded.interpretations = (
                "A literal reading treats the plot events as exactly what they seem, which usually makes the movie function as a tightly engineered external mystery or thriller with a concrete solution. "
                "A second reading treats the story as a psychological construction shaped by denial, guilt, memory, manipulation, or dream logic, which shifts the focus away from plot mechanics and toward what the narrative says about the protagonist's inner state. "
                "The reason both readings survive is that films like this are often built to support them at the same time: the literal reading explains the surface events, while the psychological reading explains why those events are framed, paced, and emotionally loaded in such a particular way. "
                "That tension is usually what keeps the ending alive in discussion long after the movie ends."
            )

        return expanded

    def _extract_sentences(self, text: str, max_sentences: int) -> str:
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip()
        parts = re.split(r"(?<=[.!?])\s+", normalized)
        selected = [part.strip() for part in parts if part.strip()][:max_sentences]
        return " ".join(selected)

    def _build_hidden_details_small(self, title: str, evidence: list[EvidenceChunk], watched: bool) -> str:
        text = " ".join(chunk.text for chunk in evidence)
        lowered = text.lower()
        concrete_bits: list[str] = []
        for label in ("director:", "actors:", "top cast:", "genre:", "genres:", "tagline:"):
            index = lowered.find(label)
            if index != -1:
                snippet = text[index:index + 140].strip()
                concrete_bits.append(snippet)
        if concrete_bits:
            joined = " ".join(concrete_bits[:2])
            return shorten(joined, width=460 if watched else 260, placeholder="…")
        return self._default_hidden_details(title, watched)
