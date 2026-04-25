from __future__ import annotations

import logging
import time
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Conflict, InvalidToken, NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bootstrap import build_pipeline
from app.config import settings
from app.models.schemas import UserQuery
from app.observability import bind_log_context, configure_logging, elapsed_ms, event_message, log_event, log_exception
from app.services.pipeline import ExplainPipeline

configure_logging(settings.log_level)

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

pipeline: ExplainPipeline | None = None

SELECTION_STATE_KEY = "selection_state"

WATCHED_SECTION_TITLES = {
    "summary": "What is really happening",
    "ending_explained": "Possible ending explanations",
    "hidden_details": "Clues, fan facts, and details people miss",
    "interpretations": "Main debates and disputed interpretations",
}

UNWATCHED_SECTION_TITLES = {
    "summary": "Why this movie may hook you",
    "ending_explained": "Spoiler-free ending note",
    "hidden_details": "What makes it interesting",
    "interpretations": "Themes and what kind of viewer may enjoy it",
}


def _new_selection_id() -> str:
    return uuid4().hex[:8]


def _movie_payload(title: str, year: int | None) -> dict[str, str | int | None]:
    return {
        "title": title,
        "year": year,
        "resolved_title": f"{title} {year}" if year else title,
    }


def _selection_state(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    state = context.user_data.get(SELECTION_STATE_KEY)
    return state if isinstance(state, dict) else None


def _clear_selection_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(SELECTION_STATE_KEY, None)


def _start_selection_state(context: ContextTypes.DEFAULT_TYPE) -> str:
    selection_id = _new_selection_id()
    context.user_data[SELECTION_STATE_KEY] = {
        "selection_id": selection_id,
        "candidate_options": [],
        "selected_movie": None,
        "watched": None,
    }
    return selection_id


def _selection_matches(context: ContextTypes.DEFAULT_TYPE, selection_id: str) -> bool:
    state = _selection_state(context)
    return bool(state and state.get("selection_id") == selection_id)


def _set_candidate_options(context: ContextTypes.DEFAULT_TYPE, selection_id: str, candidates) -> None:
    context.user_data[SELECTION_STATE_KEY] = {
        "selection_id": selection_id,
        "candidate_options": [_movie_payload(candidate.title, candidate.year) for candidate in candidates[:5]],
        "selected_movie": None,
        "watched": None,
    }


def _get_candidate_option(context: ContextTypes.DEFAULT_TYPE, selection_id: str, index: int) -> dict | None:
    state = _selection_state(context)
    if not state or state.get("selection_id") != selection_id:
        return None

    options = state.get("candidate_options", [])
    if not isinstance(options, list) or index < 0 or index >= len(options):
        return None
    option = options[index]
    return option if isinstance(option, dict) else None


def _set_selected_movie(
    context: ContextTypes.DEFAULT_TYPE,
    selection_id: str,
    *,
    title: str,
    year: int | None,
) -> None:
    state = _selection_state(context)
    if not state or state.get("selection_id") != selection_id:
        state = {
            "selection_id": selection_id,
            "candidate_options": [],
            "selected_movie": None,
            "watched": None,
        }

    state["selected_movie"] = _movie_payload(title, year)
    state["candidate_options"] = []
    state["watched"] = None
    context.user_data[SELECTION_STATE_KEY] = state


def _selected_movie(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    state = _selection_state(context)
    movie = state.get("selected_movie") if state else None
    return movie if isinstance(movie, dict) else None


def _set_watched_mode(context: ContextTypes.DEFAULT_TYPE, selection_id: str, watched: bool) -> bool:
    state = _selection_state(context)
    if not state or state.get("selection_id") != selection_id or not state.get("selected_movie"):
        return False
    state["watched"] = watched
    context.user_data[SELECTION_STATE_KEY] = state
    return True


def _watched_mode(context: ContextTypes.DEFAULT_TYPE) -> bool | None:
    state = _selection_state(context)
    watched = state.get("watched") if state else None
    return watched if isinstance(watched, bool) else None


async def _notify_selection_expired(update: Update) -> None:
    if update.callback_query:
        await update.callback_query.answer(
            "That movie selection expired. Send the title again.",
            show_alert=True,
        )


def _choice_keyboard(selection_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("I've watched it", callback_data=f"watched:{selection_id}:yes")],
        [InlineKeyboardButton("I haven't watched it", callback_data=f"watched:{selection_id}:no")],
        [InlineKeyboardButton("Search another movie", callback_data=f"search:{selection_id}:another")],
    ]
    return InlineKeyboardMarkup(buttons)


def _candidate_keyboard(candidates, selection_id: str) -> InlineKeyboardMarkup:
    buttons = []
    for index, candidate in enumerate(candidates[:5]):
        label = f"{candidate.title} ({candidate.year or 'n/a'})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"pick:{selection_id}:{index}")])
    buttons.append([InlineKeyboardButton("Search another movie", callback_data=f"search:{selection_id}:another")])
    return InlineKeyboardMarkup(buttons)


def _mode_keyboard(selection_id: str, watched: bool) -> InlineKeyboardMarkup:
    buttons = []
    if watched:
        buttons.extend(
            [
                [InlineKeyboardButton("Bigger what-is-really-happening section", callback_data=f"expand:{selection_id}:summary")],
                [InlineKeyboardButton("Bigger ending explanations", callback_data=f"expand:{selection_id}:ending_explained")],
                [InlineKeyboardButton("More clues and fan facts", callback_data=f"expand:{selection_id}:hidden_details")],
                [InlineKeyboardButton("More debates and theories", callback_data=f"expand:{selection_id}:interpretations")],
                [InlineKeyboardButton("Switch to not watched mode", callback_data=f"watched:{selection_id}:no")],
            ]
        )
    else:
        buttons.extend(
            [
                [InlineKeyboardButton("Bigger why-watch-it section", callback_data=f"expand:{selection_id}:summary")],
                [InlineKeyboardButton("Bigger spoiler-free ending note", callback_data=f"expand:{selection_id}:ending_explained")],
                [InlineKeyboardButton("More on what makes it interesting", callback_data=f"expand:{selection_id}:hidden_details")],
                [InlineKeyboardButton("More on themes and tone", callback_data=f"expand:{selection_id}:interpretations")],
                [InlineKeyboardButton("Switch to watched mode", callback_data=f"watched:{selection_id}:yes")],
            ]
        )
    buttons.append([InlineKeyboardButton("Search another movie", callback_data=f"search:{selection_id}:another")])
    return InlineKeyboardMarkup(buttons)


def _section_titles(watched: bool) -> dict[str, str]:
    return WATCHED_SECTION_TITLES if watched else UNWATCHED_SECTION_TITLES


def _get_pipeline() -> ExplainPipeline:
    global pipeline
    if pipeline is None:
        pipeline = build_pipeline()
    return pipeline


def _format_full_response(title: str, watched: bool, explanation) -> str:
    section_titles = _section_titles(watched)
    mode_label = "Watched mode" if watched else "Not watched mode"
    return (
        f"*{explanation.canonical_title}*\n"
        f"\n_{mode_label}_\n"
        f"\n*{section_titles['summary']}*\n{explanation.summary}\n"
        f"\n*{section_titles['hidden_details']}*\n{explanation.hidden_details}\n"
        f"\n*{section_titles['ending_explained']}*\n{explanation.ending_explained}\n"
        f"\n*{section_titles['interpretations']}*\n{explanation.interpretations}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        _clear_selection_state(context)
        with bind_log_context(request_id=f"tg-{update.update_id}", channel="telegram"):
            log_event(
                logger,
                logging.INFO,
                "telegram_start_command",
                update_id=update.update_id,
                chat_id=getattr(update.effective_chat, "id", None),
                user_id=getattr(update.effective_user, "id", None),
            )
        await update.message.reply_text("Send a movie title (e.g., Shutter Island).")


async def on_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    request_started_at = time.perf_counter()
    with bind_log_context(request_id=f"tg-{update.update_id}", channel="telegram"):
        current_pipeline = _get_pipeline()
        _clear_selection_state(context)
        selection_id = _start_selection_state(context)
        title = update.message.text.strip()
        log_event(
            logger,
            logging.INFO,
            "telegram_title_received",
            update_id=update.update_id,
            chat_id=getattr(update.effective_chat, "id", None),
            user_id=getattr(update.effective_user, "id", None),
            title=title,
        )
        candidates = await current_pipeline.search_service.search(title)

        if not candidates:
            _clear_selection_state(context)
            log_event(
                logger,
                logging.INFO,
                "telegram_title_no_match",
                title=title,
                elapsed_ms=elapsed_ms(request_started_at),
            )
            await update.message.reply_text("I couldn't find a strong movie match. Try the exact title, optionally with the year.")
            return

        top = candidates[0]
        if len(candidates) > 1 and top.confidence < 0.86:
            _set_candidate_options(context, selection_id, candidates)
            log_event(
                logger,
                logging.INFO,
                "telegram_title_disambiguation",
                title=title,
                candidates=[candidate.title for candidate in candidates[:5]],
                elapsed_ms=elapsed_ms(request_started_at),
            )
            await update.message.reply_text(
                "I found a few close matches. Pick the right movie:",
                reply_markup=_candidate_keyboard(candidates, selection_id),
            )
            return

        _set_selected_movie(context, selection_id, title=top.title, year=top.year)
        log_event(
            logger,
            logging.INFO,
            "telegram_title_resolved",
            query_title=title,
            matched_title=top.title,
            matched_year=top.year,
            confidence=top.confidence,
            elapsed_ms=elapsed_ms(request_started_at),
        )
        await _send_watch_prompt(update.message.reply_text, top.title, selection_id)


async def _send_watch_prompt(reply_fn, display_title: str, selection_id: str) -> None:
    prompt = (
        f"*{display_title}*\n\n"
        "Have you already watched this movie?\n"
        "Choose one option and I will either keep it spoiler-free or explain the endings, clues, and debates."
    )
    await reply_fn(prompt, parse_mode="Markdown", reply_markup=_choice_keyboard(selection_id))


async def _safe_edit_callback_message(update: Update, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if not update.callback_query:
        return
    try:
        await update.callback_query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            await update.callback_query.answer("Already showing the latest version.", show_alert=False)
            return
        log_exception(
            logger,
            "telegram_message_edit_failed",
            update_id=update.update_id,
            callback_data=update.callback_query.data,
        )
        raise


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.callback_query.data:
        return

    callback_started_at = time.perf_counter()
    with bind_log_context(request_id=f"tg-{update.update_id}", channel="telegram"):
        current_pipeline = _get_pipeline()
        await update.callback_query.answer()
        action, _, remainder = update.callback_query.data.partition(":")
        log_event(
            logger,
            logging.INFO,
            "telegram_callback_received",
            update_id=update.update_id,
            chat_id=getattr(update.effective_chat, "id", None),
            user_id=getattr(update.effective_user, "id", None),
            action=action,
            callback_data=update.callback_query.data,
        )

        if action == "search":
            _clear_selection_state(context)
            await _safe_edit_callback_message(
                update,
                "Send me another movie title and I’ll start a new search.",
            )
            log_event(
                logger,
                logging.INFO,
                "telegram_callback_completed",
                action=action,
                elapsed_ms=elapsed_ms(callback_started_at),
            )
            return

        if action == "pick":
            selection_id, _, index_str = remainder.partition(":")
            if not selection_id or not index_str.isdigit() or not _selection_matches(context, selection_id):
                await _notify_selection_expired(update)
                return

            option = _get_candidate_option(context, selection_id, int(index_str))
            if not option:
                await _notify_selection_expired(update)
                return

            display_title = str(option["title"])
            _set_selected_movie(
                context,
                selection_id,
                title=display_title,
                year=option.get("year"),
            )
            await _safe_edit_callback_message(
                update,
                f"*{display_title}*\n\nHave you already watched this movie?\nChoose one option and I will tailor the explanation.",
                reply_markup=_choice_keyboard(selection_id),
            )
            log_event(
                logger,
                logging.INFO,
                "telegram_callback_completed",
                action=action,
                selection_id=selection_id,
                resolved_title=option.get("resolved_title"),
                elapsed_ms=elapsed_ms(callback_started_at),
            )
            return

        if action == "watched":
            selection_id, _, watched_value = remainder.partition(":")
            if watched_value not in {"yes", "no"} or not _selection_matches(context, selection_id):
                await _notify_selection_expired(update)
                return

            selected_movie = _selected_movie(context)
            if not selected_movie:
                await _notify_selection_expired(update)
                return

            watched = watched_value == "yes"
            _set_watched_mode(context, selection_id, watched)
            title = str(selected_movie["resolved_title"])
            mode = "ending_explained" if watched else "no_spoilers"
            response = await current_pipeline.run(
                UserQuery(
                    title=title,
                    mode=mode,
                    allow_spoilers=watched,
                    watched=watched,
                )
            )
            if not response.explanation:
                await _safe_edit_callback_message(update, "No explanation available.")
                log_event(
                    logger,
                    logging.INFO,
                    "telegram_callback_completed",
                    action=action,
                    title=title,
                    watched=watched,
                    explanation_available=False,
                    elapsed_ms=elapsed_ms(callback_started_at),
                )
                return

            explanation = response.explanation
            text = _format_full_response(title, watched, explanation)
            await _safe_edit_callback_message(update, text, reply_markup=_mode_keyboard(selection_id, watched))
            log_event(
                logger,
                logging.INFO,
                "telegram_callback_completed",
                action=action,
                title=title,
                watched=watched,
                spoiler_level=explanation.spoiler_level,
                elapsed_ms=elapsed_ms(callback_started_at),
            )
            return

        if action != "expand":
            await _notify_selection_expired(update)
            return

        selection_id, _, mode = remainder.partition(":")
        if not mode or not _selection_matches(context, selection_id):
            await _notify_selection_expired(update)
            return

        selected_movie = _selected_movie(context)
        watched = _watched_mode(context)
        if not selected_movie or watched is None:
            await _notify_selection_expired(update)
            return

        title = str(selected_movie["resolved_title"])
        allow_spoilers = watched
        response = await current_pipeline.run(
            UserQuery(
                title=title,
                mode="ending_explained" if mode == "summary" and watched else mode,
                allow_spoilers=allow_spoilers,
                watched=watched,
                detail_level="expanded",
                focus_section=mode if mode != "no_spoilers" else "summary",
            )
        )
        if not response.explanation:
            await _safe_edit_callback_message(update, "No explanation available.")
            log_event(
                logger,
                logging.INFO,
                "telegram_callback_completed",
                action=action,
                title=title,
                mode=mode,
                watched=watched,
                explanation_available=False,
                elapsed_ms=elapsed_ms(callback_started_at),
            )
            return

        explanation = response.explanation
        field_name = mode if mode != "no_spoilers" else "summary"
        section_title = _section_titles(watched).get(field_name, field_name.replace("_", " ").title())
        body = getattr(explanation, field_name)
        text = f"*{explanation.canonical_title}*\n\n*{section_title}*\n{body}"
        await _safe_edit_callback_message(update, text, reply_markup=_mode_keyboard(selection_id, watched))
        log_event(
            logger,
            logging.INFO,
            "telegram_callback_completed",
            action=action,
            title=title,
            mode=mode,
            watched=watched,
            spoiler_level=explanation.spoiler_level,
            elapsed_ms=elapsed_ms(callback_started_at),
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_id = getattr(update, "update_id", None)
    with bind_log_context(request_id=f"tg-{update_id}" if update_id is not None else None, channel="telegram"):
        error = context.error
        if isinstance(error, Conflict):
            log_event(
                logger,
                logging.ERROR,
                "telegram_polling_conflict",
                update_id=update_id,
                update_type=type(update).__name__ if update is not None else None,
                message="another bot instance is already polling; shutting down this process",
                error=str(error),
            )
            context.application.stop_running()
            return

        logger.error(
            event_message(
                "telegram_handler_failed",
                update_id=update_id,
                update_type=type(update).__name__ if update is not None else None,
                error=str(error) if error else None,
            ),
            exc_info=(type(error), error, error.__traceback__) if error is not None else None,
        )


def run_bot() -> None:
    global pipeline
    if not settings.has_telegram_token():
        raise RuntimeError("telegram_token is required in app/config.py")

    pipeline = build_pipeline()
    log_event(logger, logging.INFO, "telegram_bot_starting")

    app = Application.builder().token(settings.telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_title))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    try:
        app.run_polling()
        log_event(logger, logging.INFO, "telegram_bot_stopped")
    except InvalidToken:
        log_event(
            logger,
            logging.ERROR,
            "telegram_startup_failed",
            reason="invalid_token",
            message="configured bot token is invalid",
        )
        raise SystemExit(1)
    except NetworkError as exc:
        log_event(
            logger,
            logging.ERROR,
            "telegram_startup_failed",
            reason="network_error",
            message="could not reach Telegram",
            error=str(exc),
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run_bot()
