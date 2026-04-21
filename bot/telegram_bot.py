from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bootstrap import build_pipeline
from app.config import settings
from app.models.schemas import UserQuery

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

pipeline = build_pipeline()


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


def _choice_keyboard(title: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("I've watched it", callback_data=f"watched:yes:{title}")],
        [InlineKeyboardButton("I haven't watched it", callback_data=f"watched:no:{title}")],
        [InlineKeyboardButton("Search another movie", callback_data="search:another")],
    ]
    return InlineKeyboardMarkup(buttons)


def _candidate_keyboard(candidates) -> InlineKeyboardMarkup:
    buttons = []
    for candidate in candidates[:5]:
        label = f"{candidate.title} ({candidate.year or 'n/a'})"
        resolved_title = f"{candidate.title} {candidate.year}" if candidate.year else candidate.title
        buttons.append([InlineKeyboardButton(label, callback_data=f"pick:{resolved_title}")])
    buttons.append([InlineKeyboardButton("Search another movie", callback_data="search:another")])
    return InlineKeyboardMarkup(buttons)


def _mode_keyboard(title: str, watched: bool) -> InlineKeyboardMarkup:
    buttons = []
    if watched:
        buttons.extend(
            [
                [InlineKeyboardButton("Bigger what-is-really-happening section", callback_data=f"expand:watched:summary:{title}")],
                [InlineKeyboardButton("Bigger ending explanations", callback_data=f"expand:watched:ending_explained:{title}")],
                [InlineKeyboardButton("More clues and fan facts", callback_data=f"expand:watched:hidden_details:{title}")],
                [InlineKeyboardButton("More debates and theories", callback_data=f"expand:watched:interpretations:{title}")],
                [InlineKeyboardButton("Switch to not watched mode", callback_data=f"watched:no:{title}")],
            ]
        )
    else:
        buttons.extend(
            [
                [InlineKeyboardButton("Bigger why-watch-it section", callback_data=f"expand:not_watched:summary:{title}")],
                [InlineKeyboardButton("Bigger spoiler-free ending note", callback_data=f"expand:not_watched:ending_explained:{title}")],
                [InlineKeyboardButton("More on what makes it interesting", callback_data=f"expand:not_watched:hidden_details:{title}")],
                [InlineKeyboardButton("More on themes and tone", callback_data=f"expand:not_watched:interpretations:{title}")],
                [InlineKeyboardButton("Switch to watched mode", callback_data=f"watched:yes:{title}")],
            ]
        )
    buttons.append([InlineKeyboardButton("Search another movie", callback_data="search:another")])
    return InlineKeyboardMarkup(buttons)


def _section_titles(watched: bool) -> dict[str, str]:
    return WATCHED_SECTION_TITLES if watched else UNWATCHED_SECTION_TITLES


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
        await update.message.reply_text("Send a movie title (e.g., Shutter Island).")


async def on_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    title = update.message.text.strip()
    candidates = await pipeline.search_service.search(title)

    if not candidates:
        await update.message.reply_text("I couldn't find a strong movie match. Try the exact title, optionally with the year.")
        return

    top = candidates[0]
    if len(candidates) > 1 and top.confidence < 0.86:
        await update.message.reply_text(
            "I found a few close matches. Pick the right movie:",
            reply_markup=_candidate_keyboard(candidates),
        )
        return

    resolved_title = f"{top.title} {top.year}" if top.year else top.title
    await _send_watch_prompt(update.message.reply_text, top.title, resolved_title)


async def _send_watch_prompt(reply_fn, display_title: str, resolved_title: str) -> None:
    prompt = (
        f"*{display_title}*\n\n"
        "Have you already watched this movie?\n"
        "Choose one option and I will either keep it spoiler-free or explain the endings, clues, and debates."
    )
    await reply_fn(prompt, parse_mode="Markdown", reply_markup=_choice_keyboard(resolved_title))


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
        raise


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.callback_query.data:
        return

    await update.callback_query.answer()
    parts = update.callback_query.data.split(":", 3)
    action = parts[0]

    if action == "search":
        await _safe_edit_callback_message(
            update,
            "Send me another movie title and I’ll start a new search.",
        )
        return

    if action == "pick":
        resolved_title = parts[1]
        display_title, _ = pipeline.search_service.split_title_and_year(resolved_title)
        await _safe_edit_callback_message(
            update,
            f"*{display_title}*\n\nHave you already watched this movie?\nChoose one option and I will tailor the explanation.",
            reply_markup=_choice_keyboard(resolved_title),
        )
        return

    if action == "watched":
        watched = parts[1] == "yes"
        title = parts[2]
        mode = "ending_explained" if watched else "no_spoilers"
        response = await pipeline.run(
            UserQuery(
                title=title,
                mode=mode,
                allow_spoilers=watched,
                watched=watched,
            )
        )
        if not response.explanation:
            await _safe_edit_callback_message(update, "No explanation available.")
            return

        explanation = response.explanation
        text = _format_full_response(title, watched, explanation)
        await _safe_edit_callback_message(update, text, reply_markup=_mode_keyboard(title, watched))
        return

    _, watched_key, mode, title = parts
    watched = watched_key == "watched"
    allow_spoilers = watched
    response = await pipeline.run(
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
        return

    explanation = response.explanation
    field_name = mode if mode != "no_spoilers" else "summary"
    section_title = _section_titles(watched).get(field_name, field_name.replace("_", " ").title())
    body = getattr(explanation, field_name)
    text = f"*{explanation.canonical_title}*\n\n*{section_title}*\n{body}"
    await _safe_edit_callback_message(update, text, reply_markup=_mode_keyboard(title, watched))


def run_bot() -> None:
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    app = Application.builder().token(settings.telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_title))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.run_polling()


if __name__ == "__main__":
    run_bot()
