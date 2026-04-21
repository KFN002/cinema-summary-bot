from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bootstrap import build_pipeline
from app.config import settings
from app.models.schemas import UserQuery

logging.basicConfig(level=logging.INFO)

pipeline = build_pipeline()


def _mode_keyboard(title: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("No spoilers", callback_data=f"mode:no_spoilers:{title}")],
        [InlineKeyboardButton("Ending explained", callback_data=f"mode:ending_explained:{title}")],
        [InlineKeyboardButton("Hidden details", callback_data=f"mode:hidden_details:{title}")],
        [InlineKeyboardButton("Interpretations", callback_data=f"mode:interpretations:{title}")],
        [InlineKeyboardButton("Show spoilers", callback_data=f"spoiler:on:{title}")],
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Send a movie title (e.g., Shutter Island).")


async def on_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    title = update.message.text.strip()
    response = await pipeline.run(UserQuery(title=title, mode="no_spoilers", allow_spoilers=False))

    if response.requires_disambiguation:
        options = "\n".join(
            f"• {candidate.title} ({candidate.year or 'n/a'}) — confidence {candidate.confidence}"
            for candidate in response.candidates
        )
        await update.message.reply_text(f"Multiple matches found:\n{options}\n\nPlease send exact title + year.")
        return

    if not response.explanation:
        await update.message.reply_text("Not enough source evidence found.")
        return

    explanation = response.explanation
    reply = (
        f"*{explanation.canonical_title}*\n"
        f"\n*Summary*\n{explanation.summary}\n"
        f"\n*Ending explained*\n{explanation.ending_explained}\n"
        f"\n*Hidden details*\n{explanation.hidden_details}\n"
        f"\n*Interpretations*\n{explanation.interpretations}\n"
        f"\nCache: {'hit' if explanation.from_cache else 'fresh'}"
    )
    await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=_mode_keyboard(title))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.callback_query.data:
        return

    await update.callback_query.answer()
    action, value, title = update.callback_query.data.split(":", 2)
    allow_spoilers = action == "spoiler" and value == "on"
    mode = value if action == "mode" else "ending_explained"

    response = await pipeline.run(UserQuery(title=title, mode=mode, allow_spoilers=allow_spoilers))
    if not response.explanation:
        await update.callback_query.edit_message_text("No explanation available.")
        return

    explanation = response.explanation
    body = getattr(explanation, mode if mode != "no_spoilers" else "summary")
    text = f"*{explanation.canonical_title}*\n\n*{mode.replace('_', ' ').title()}*\n{body}"
    await update.callback_query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=_mode_keyboard(title))


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
