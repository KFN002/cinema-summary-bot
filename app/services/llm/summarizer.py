from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from textwrap import shorten

from app.config import settings
from app.models.schemas import EvidenceChunk, MovieExplanation
from app.observability import balance_snapshot, elapsed_ms, log_event, log_exception

logger = logging.getLogger(__name__)


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
        log_event(
            logger,
            logging.INFO,
            "ai_summary_started",
            provider="local",
            title=title,
            year=year,
            allow_spoilers=allow_spoilers,
            watched=watched,
            detail_level=detail_level,
            focus_section=focus_section,
            evidence_chunks=len(evidence),
        )
        base_explanation = self._summarize_small(title, year, evidence, allow_spoilers, watched)
        if detail_level == "expanded" and not evidence and self._can_use_gigachat():
            try:
                base_explanation = await asyncio.to_thread(
                    self._compose_standard_with_gigachat,
                    base_explanation,
                    title,
                    year,
                    evidence,
                    allow_spoilers,
                    watched,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "ai_base_summary_upgraded",
                    provider="gigachat",
                    title=title,
                    year=year,
                    detail_level=detail_level,
                    focus_section=focus_section,
                )
            except Exception:
                log_exception(
                    logger,
                    "ai_request_failed",
                    provider="gigachat",
                    title=title,
                    year=year,
                    detail_level=detail_level,
                    focus_section="base_upgrade",
                )
        if detail_level == "standard" and self._can_use_gigachat() and ((watched and allow_spoilers) or not evidence):
            try:
                explanation = await asyncio.to_thread(
                    self._compose_standard_with_gigachat,
                    base_explanation,
                    title,
                    year,
                    evidence,
                    allow_spoilers,
                    watched,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "ai_summary_completed",
                    provider="gigachat",
                    title=title,
                    year=year,
                    detail_level=detail_level,
                    focus_section=focus_section,
                    spoiler_level=explanation.spoiler_level,
                )
                return explanation
            except Exception:
                log_exception(
                    logger,
                    "ai_request_failed",
                    provider="gigachat",
                    title=title,
                    year=year,
                    detail_level=detail_level,
                    focus_section="full_response",
                )

        if detail_level != "expanded" or not focus_section:
            log_event(
                logger,
                logging.INFO,
                "ai_summary_completed",
                provider="local",
                title=title,
                year=year,
                detail_level=detail_level,
                focus_section=focus_section,
                spoiler_level=base_explanation.spoiler_level,
            )
            return base_explanation

        if self._can_use_gigachat():
            try:
                explanation = await asyncio.to_thread(
                    self._expand_with_gigachat,
                    base_explanation,
                    title,
                    year,
                    evidence,
                    allow_spoilers,
                    watched,
                    focus_section,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "ai_summary_completed",
                    provider="gigachat",
                    title=title,
                    year=year,
                    detail_level=detail_level,
                    focus_section=focus_section,
                    spoiler_level=explanation.spoiler_level,
                )
                return explanation
            except Exception:
                log_exception(
                    logger,
                    "ai_request_failed",
                    provider="gigachat",
                    title=title,
                    year=year,
                    detail_level=detail_level,
                    focus_section=focus_section,
                )

        log_event(
            logger,
            logging.INFO,
            "ai_fallback_used",
            provider="local",
            reason="gigachat_unavailable_or_failed",
            title=title,
            year=year,
            focus_section=focus_section,
        )
        fallback_explanation = self._expand_fallback(base_explanation, title, evidence, allow_spoilers, watched, focus_section)
        log_event(
            logger,
            logging.INFO,
            "ai_summary_completed",
            provider="local",
            title=title,
            year=year,
            detail_level=detail_level,
            focus_section=focus_section,
            spoiler_level=fallback_explanation.spoiler_level,
        )
        return fallback_explanation

    def _can_use_gigachat(self) -> bool:
        return settings.has_gigachat_credentials()

    def _serialize_evidence(self, evidence: list[EvidenceChunk]) -> str:
        return "\n\n".join(
            (
                f"Source: {chunk.source_name}\n"
                f"URL: {chunk.source_url}\n"
                f"Spoiler: {'yes' if chunk.spoiler else 'no'}\n"
                f"Text: {chunk.text}"
            )
            for chunk in evidence
        )

    def _coerce_spoiler_level(self, payload: dict[str, str], allow_spoilers: bool) -> str:
        spoiler_level = payload.get("spoiler_level", "none")
        if spoiler_level not in {"none", "light", "full"}:
            return "full" if allow_spoilers else "none"
        if not allow_spoilers and spoiler_level != "none":
            return "none"
        return spoiler_level

    def _run_gigachat_request(
        self,
        *,
        operation: str,
        title: str,
        year: int | None,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
        watched: bool,
        focus_section: str | None,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 3200,
    ) -> dict[str, str]:
        from gigachat import GigaChat
        from gigachat.models import Chat, Messages

        payload = Chat(
            model=settings.gigachat_model,
            temperature=0.2,
            max_tokens=max_tokens,
            messages=[
                Messages(role="system", content=system_prompt),
                Messages(role="user", content=user_prompt),
            ],
        )

        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "ai_request_started",
            provider="gigachat",
            operation=operation,
            model=settings.gigachat_model,
            title=title,
            year=year,
            watched=watched,
            allow_spoilers=allow_spoilers,
            focus_section=focus_section,
            evidence_chunks=len(evidence),
            system_prompt_chars=len(system_prompt),
            user_prompt_chars=len(user_prompt),
            max_tokens=payload.max_tokens,
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
            balance = None
            if settings.gigachat_log_balance:
                try:
                    balance = client.get_balance()
                except Exception:
                    log_exception(
                        logger,
                        "ai_balance_fetch_failed",
                        provider="gigachat",
                        title=title,
                        year=year,
                        focus_section=focus_section,
                    )

        content = response.choices[0].message.content
        usage = response.usage
        x_headers = response.x_headers or {}
        log_event(
            logger,
            logging.INFO,
            "ai_request_completed",
            provider="gigachat",
            operation=operation,
            model=response.model,
            title=title,
            year=year,
            elapsed_ms=elapsed_ms(started_at),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            precached_prompt_tokens=usage.precached_prompt_tokens,
            response_chars=len(content),
            request_headers=x_headers,
            remaining_balance=balance_snapshot(balance) if balance is not None else None,
        )
        return self._extract_json(content)

    def _compose_standard_with_gigachat(
        self,
        base_explanation: MovieExplanation,
        title: str,
        year: int | None,
        evidence: list[EvidenceChunk],
        allow_spoilers: bool,
        watched: bool,
    ) -> MovieExplanation:
        movie_label = f"{title} ({year})" if year else title
        serialized_evidence = self._serialize_evidence(evidence)
        evidence_instruction = (
            f"Evidence:\n{serialized_evidence}"
            if serialized_evidence
            else "Evidence: none. Rely on broad, widely known movie knowledge and mainstream interpretations."
        )
        spoiler_policy = (
            "Spoilers are fully allowed. Be direct about reveals, identities, and the ending."
            if allow_spoilers
            else "Spoilers are not allowed. Do not reveal the ending, twist, secret identity, killer, or final fate."
        )
        audience_goal = (
            "The user has already watched the movie, so the explanation should be concrete and direct."
            if watched
            else "The user has not watched the movie yet, so every section must stay spoiler-safe while still being vivid and useful."
        )
        system_prompt = (
            "You are a movie explanation assistant. "
            "Use the supplied evidence first, but if the evidence is missing or thin, rely on broad, widely known movie knowledge and mainstream interpretations. "
            "Do not fabricate obscure scene details or fake citations. "
            "Be concrete and useful rather than abstract. "
            "Return strict JSON with keys: summary, ending_explained, hidden_details, interpretations, spoiler_level. "
            "Output JSON only, without markdown fences."
        )
        user_prompt = (
            f"Movie: {movie_label}\n"
            f"Audience mode: {'watched' if watched else 'not_watched'}\n"
            f"{spoiler_policy}\n"
            f"{audience_goal}\n\n"
            "Write all four sections.\n\n"
            "Rules:\n"
            "- `summary` must be specific and should explain what the movie is really about for this audience mode.\n"
            "- `ending_explained` must explain the payoff clearly when spoilers are allowed, or stay spoiler-safe when they are not.\n"
            "- `hidden_details` must mention concrete clues, motifs, performances, or widely discussed production/story details.\n"
            "- `interpretations` should explain the main competing readings if the ending is debated.\n"
            "- Never answer with vague filler like 'things are not what they seem' without explaining the actual point.\n"
            "- If evidence is missing, still provide a useful answer from broad movie knowledge.\n"
            "- Keep each section concise but substantial.\n"
            f"- spoiler_level must be `{'full' if allow_spoilers else 'none'}`.\n\n"
            f"Current fallback draft:\n"
            f"- summary: {base_explanation.summary}\n"
            f"- ending_explained: {base_explanation.ending_explained}\n"
            f"- hidden_details: {base_explanation.hidden_details}\n"
            f"- interpretations: {base_explanation.interpretations}\n\n"
            f"{evidence_instruction}"
        )
        payload_dict = self._run_gigachat_request(
            operation="standard_summary",
            title=title,
            year=year,
            evidence=evidence,
            allow_spoilers=allow_spoilers,
            watched=watched,
            focus_section="full_response",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary=self._coerce_section_text(payload_dict, "summary", base_explanation.summary),
            ending_explained=self._coerce_section_text(payload_dict, "ending_explained", base_explanation.ending_explained),
            hidden_details=self._coerce_section_text(payload_dict, "hidden_details", base_explanation.hidden_details),
            interpretations=self._coerce_section_text(payload_dict, "interpretations", base_explanation.interpretations),
            spoiler_level=self._coerce_spoiler_level(payload_dict, allow_spoilers=allow_spoilers),
            evidence=evidence,
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
        movie_label = f"{title} ({year})" if year else title
        serialized_evidence = self._serialize_evidence(evidence)
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
            "- Each section value must be a single plain string, not a JSON list, object, outline, or nested structure.\n"
            "- Keep the three non-focused sections close to the current small version.\n"
            "- Never answer with 'not enough evidence' or other filler if a useful movie explanation can still be produced.\n"
            "- If evidence is missing, lean on broad, mainstream movie knowledge and give a concrete answer anyway.\n"
            "- spoiler_level must be one of: none, light, full.\n\n"
            f"Evidence:\n{serialized_evidence or 'None supplied. Use broad movie knowledge.'}"
        )
        payload_dict = self._run_gigachat_request(
            operation="focused_expand",
            title=title,
            year=year,
            evidence=evidence,
            allow_spoilers=allow_spoilers,
            watched=watched,
            focus_section=focus_section,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        return MovieExplanation(
            canonical_title=title,
            year=year,
            summary=self._coerce_section_text(payload_dict, "summary", base_explanation.summary),
            ending_explained=self._coerce_section_text(payload_dict, "ending_explained", base_explanation.ending_explained),
            hidden_details=self._coerce_section_text(payload_dict, "hidden_details", base_explanation.hidden_details),
            interpretations=self._coerce_section_text(payload_dict, "interpretations", base_explanation.interpretations),
            spoiler_level=self._coerce_spoiler_level(payload_dict, allow_spoilers=allow_spoilers),
            evidence=evidence,
        )

    def _extract_json(self, content: str) -> dict[str, object]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1:
            log_event(
                logger,
                logging.ERROR,
                "ai_response_parse_failed",
                provider="gigachat",
                reason="json_not_found",
                response_preview=shorten(stripped, width=200, placeholder="..."),
            )
            raise ValueError("GigaChat response does not contain JSON")

        payload = json.loads(stripped[start : end + 1])
        if not isinstance(payload, dict):
            log_event(
                logger,
                logging.ERROR,
                "ai_response_parse_failed",
                provider="gigachat",
                reason="json_not_object",
                response_preview=shorten(str(payload), width=200, placeholder="..."),
            )
            raise ValueError("GigaChat JSON payload must be an object")
        return payload

    def _coerce_section_text(self, payload: dict[str, object], key: str, fallback: str) -> str:
        raw_value = payload.get(key)
        text = self._flatten_section_value(raw_value).strip()
        if text:
            return shorten(text, width=2600, placeholder="…")
        return fallback

    def _flatten_section_value(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            parts = [self._flatten_section_value(item) for item in value]
            cleaned = [part for part in parts if part]
            return "\n\n".join(cleaned)
        if isinstance(value, dict):
            preferred_fields = ("title", "label", "name", "heading", "summary", "body", "text", "content", "explanation")
            pieces: list[str] = []
            used_keys: set[str] = set()
            for field in preferred_fields:
                field_value = self._flatten_section_value(value.get(field))
                if not field_value:
                    continue
                used_keys.add(field)
                if field in {"title", "label", "name", "heading"}:
                    pieces.append(field_value)
                else:
                    pieces.append(field_value)
            for field, field_value in value.items():
                if field in used_keys:
                    continue
                flattened = self._flatten_section_value(field_value)
                if not flattened:
                    continue
                label = field.replace("_", " ").strip().title()
                pieces.append(f"{label}: {flattened}")
            return "\n\n".join(piece for piece in pieces if piece)
        return str(value).strip()

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
            summary = self._build_watched_summary(title, non_spoiler_text, spoiler_text, combined_text)
            hidden_details = self._build_hidden_details_small(title, evidence, watched)
            interpretations = self._build_interpretations_small(title, spoiler_text, combined_text, watched)
        else:
            summary = lead_non_spoiler or self._default_summary(title, watched)
            hidden_details = self._build_hidden_details_small(title, evidence, watched)
            interpretations = (
                f"{title} should work best for viewers who enjoy tension, layered themes, and stories that keep raising questions."
                if non_spoiler_text
                else self._default_interpretations(title, watched)
            )

        if allow_spoilers and spoiler_text:
            ending = self._build_watched_ending(title, spoiler_text, combined_text)
            spoiler_level = "full"
        elif allow_spoilers and combined_text:
            ending = self._build_watched_ending(title, spoiler_text, combined_text)
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
            summary = shorten(summary, width=650, placeholder="…")
            ending = shorten(ending, width=650, placeholder="…")
            hidden_details = shorten(hidden_details, width=560, placeholder="…")
            interpretations = shorten(interpretations, width=560, placeholder="…")

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
                else base_explanation.summary or self._default_summary(title, watched)
            )
        elif focus_section == "ending_explained":
            expanded.ending_explained = (
                shorten(spoiler_text or combined_text, width=2200, placeholder="…")
                if (spoiler_text or combined_text)
                else base_explanation.ending_explained or self._default_ending(title)
            )
            if allow_spoilers:
                expanded.spoiler_level = "full" if spoiler_text else "light"
        elif focus_section == "hidden_details":
            expanded.hidden_details = (
                shorten(combined_text, width=2200, placeholder="…")
                if combined_text
                else base_explanation.hidden_details or self._default_hidden_details(title, watched)
            )
        elif focus_section == "interpretations":
            expanded.interpretations = (
                base_explanation.interpretations
                or
                "A literal reading treats the plot events as exactly what they seem, which usually makes the movie function as a tightly engineered external mystery or thriller with a concrete solution. "
                "A second reading treats the story as a psychological construction shaped by denial, guilt, memory, manipulation, or dream logic, which shifts the focus away from plot mechanics and toward what the narrative says about the protagonist's inner state. "
                "The reason both readings survive is that films like this are often built to support them at the same time: the literal reading explains the surface events, while the psychological reading explains why those events are framed, paced, and emotionally loaded in such a particular way. "
                "That tension is usually what keeps the ending alive in discussion long after the movie ends."
            )

        return expanded

    def _sentence_list(self, text: str) -> list[str]:
        if not text:
            return []
        normalized = re.sub(r"\s+", " ", text).strip()
        return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]

    def _extract_sentences(self, text: str, max_sentences: int) -> str:
        selected = self._sentence_list(text)[:max_sentences]
        return " ".join(selected)

    def _extract_tail_sentences(self, text: str, max_sentences: int) -> str:
        sentences = self._sentence_list(text)
        if not sentences:
            return ""
        return " ".join(sentences[-max_sentences:])

    def _extract_keyword_sentences(self, text: str, keywords: tuple[str, ...], max_sentences: int) -> str:
        matches: list[str] = []
        seen: set[str] = set()
        for sentence in self._sentence_list(text):
            lowered = sentence.lower()
            if not any(keyword in lowered for keyword in keywords):
                continue
            normalized = lowered.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            matches.append(sentence)
            if len(matches) >= max_sentences:
                break
        return " ".join(matches)

    def _build_watched_summary(
        self,
        title: str,
        non_spoiler_text: str,
        spoiler_text: str,
        combined_text: str,
    ) -> str:
        setup = self._extract_sentences(non_spoiler_text or combined_text, 2)
        reveal = self._extract_keyword_sentences(
            spoiler_text or combined_text,
            ("revealed", "actually", "really", "identity", "discovers", "learns", "finds out", "psychiatrist", "patient"),
            2,
        ) or self._extract_sentences(spoiler_text, 2)
        parts: list[str] = []
        if setup:
            parts.append(setup)
        if reveal and reveal not in parts:
            parts.append(reveal)
        return " ".join(parts) or self._default_summary(title, watched=True)

    def _build_watched_ending(self, title: str, spoiler_text: str, combined_text: str) -> str:
        ending = (
            self._extract_tail_sentences(spoiler_text, 3)
            or self._extract_keyword_sentences(
                spoiler_text or combined_text,
                ("final", "finally", "ending", "last", "ultimately", "chooses", "dies", "lobotomy", "escape"),
                3,
            )
            or self._extract_sentences(spoiler_text or combined_text, 3)
        )
        return ending or self._default_ending(title)

    def _build_interpretations_small(self, title: str, spoiler_text: str, combined_text: str, watched: bool) -> str:
        if not watched:
            return self._default_interpretations(title, watched)

        literal = self._extract_sentences(spoiler_text or combined_text, 1)
        ambiguity = self._extract_keyword_sentences(
            spoiler_text or combined_text,
            ("memory", "dream", "identity", "psychological", "reality", "delusion", "symbolic", "guilt", "hallucination"),
            2,
        )
        if literal and ambiguity:
            return (
                f"One reading takes the film literally: {literal} Another reading leans on details around {ambiguity} "
                "and treats the story as a psychological or symbolic construction rather than only a surface-level mystery."
            )
        if literal:
            return (
                f"On the literal reading, {literal} The debate comes from whether those events are plain fact "
                "or the movie's way of dramatizing guilt, denial, unstable memory, or distorted perception."
            )
        return self._default_interpretations(title, watched)

    def _build_hidden_details_small(self, title: str, evidence: list[EvidenceChunk], watched: bool) -> str:
        text = " ".join(chunk.text for chunk in evidence)
        spoiler_text = " ".join(chunk.text for chunk in evidence if chunk.spoiler)
        lowered = text.lower()
        concrete_bits: list[str] = []
        clue_bits: list[str] = []
        if watched:
            clue_sentence = self._extract_keyword_sentences(
                spoiler_text or text,
                ("identity", "memory", "dream", "hallucination", "psychiatrist", "patient", "flashback", "clue", "revealed", "doctor"),
                2,
            )
            if clue_sentence:
                clue_bits.append(clue_sentence)
        for label in ("director:", "actors:", "top cast:", "genre:", "genres:", "tagline:"):
            index = lowered.find(label)
            if index != -1:
                snippet = text[index:index + 140].strip()
                concrete_bits.append(snippet)
        if clue_bits or concrete_bits:
            joined = " ".join([*clue_bits[:1], *concrete_bits[:2]]).strip()
            return shorten(joined, width=460 if watched else 260, placeholder="…")
        return self._default_hidden_details(title, watched)
