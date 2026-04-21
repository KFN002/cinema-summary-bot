from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    db_path: str = os.getenv("DB_PATH", "cinema_summary.db")
    omdb_api_key: str = os.getenv("OMDB_API_KEY", "")
    tmdb_api_token: str = os.getenv("TMDB_API_TOKEN", "")


settings = Settings()
