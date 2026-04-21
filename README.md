# Cinema Summary Bot (MVP)

A modular-monolith MVP for a Telegram bot that provides spoiler-aware, source-grounded movie explanations.

## What this MVP does

- Accepts a movie title (Telegram or API).
- Normalizes and matches against a small movie index.
- Performs disambiguation when confidence is low.
- Retrieves source text from multiple adapters:
  - Wikipedia (always on)
  - OMDb (optional via `OMDB_API_KEY`)
  - TMDb (optional via `TMDB_API_TOKEN`)
- Merges evidence across enabled sources.
- Splits spoiler/non-spoiler evidence.
- Produces structured output sections:
  - summary
  - ending_explained
  - hidden_details
  - interpretations
- Caches processed results in SQLite.

## Project structure

- `bot/` — Telegram handlers and callback flow
- `app/services/search.py` — title normalization and fuzzy matching
- `app/services/sources/` — source adapters + aggregator
- `app/services/llm/` — grounded summarizer
- `app/services/cache/` — SQLite cache repository
- `app/models/` — Pydantic schemas
- `tests/` — normalization and cache tests

## Quick start

```bash
make install
make run-api
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Explain example:

```bash
curl -X POST http://127.0.0.1:8000/explain \
  -H "content-type: application/json" \
  -d '{"title":"Shutter Island","mode":"no_spoilers","allow_spoilers":false}'
```

## Telegram bot

Set token and run:

```bash
export TELEGRAM_BOT_TOKEN=...
make run-bot
```

## Optional source adapter keys

```bash
export OMDB_API_KEY=...
export TMDB_API_TOKEN=...
```

Without these keys, the bot still works with Wikipedia evidence only.

## Useful commands

- `make test`
- `make compile`
- `make clean`
