# Cinema Summary Bot (MVP, GigaChat)

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
- Uses GigaChat API for grounded summaries and keeps a local fallback while credentials are still placeholders.
- In Telegram, asks whether the user has already watched the movie and switches between spoiler-free recommendation mode and full spoiler explanation mode.

## Project structure

- `bot/` — Telegram handlers and callback flow
- `app/services/search.py` — title normalization and fuzzy matching
- `app/services/sources/` — source adapters + aggregator
- `app/services/llm/` — grounded summarizer
- `app/models/` — Pydantic schemas
- `tests/` — normalization and source tests

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
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

## Hardcoded config

Edit [app/config.py](/Users/kirill/PycharmProjects/cinema-summary-bot/app/config.py) and paste your values directly into the `Settings` dataclass:

- `telegram_token`
- `gigachat_credentials`
- `cache_enabled`
- `cache_ttl_seconds`
- `cache_db_path`
- `omdb_api_key`
- `tmdb_api_token`

GigaChat is configured for Russian endpoints by default:

- `base_url`: `https://gigachat.devices.sberbank.ru/api/v1`
- `auth_url`: `https://ngw.devices.sberbank.ru:9443/api/v2/oauth`
- `scope`: `GIGACHAT_API_PERS`

By default, SSL verification is disabled in config to make first-run setup easier on macOS and in RU environments where the required certificate bundle is not installed yet. For production, turn `gigachat_verify_ssl_certs` back on and set `gigachat_ca_bundle_file` if needed.

Cache is configurable in code:

- `cache_enabled = True` turns caching on or off
- `cache_ttl_seconds = 10800` keeps entries for 3 hours
- `cache_db_path` controls where the SQLite cache file is stored

## Telegram bot

```bash
make run-bot
```

## Optional source adapter keys

```bash
export OMDB_API_KEY=...
export TMDB_API_TOKEN=...
```

Without these keys, the bot still works with Wikipedia evidence only.

## macOS notes

- The project is optimized for `python3` commands and virtualenv flow on macOS.
- `make install` installs from `requirements.txt` first, then installs the package itself.
- `make run-api` uses `python3 -m uvicorn`, which is usually more reliable in macOS virtualenvs than a bare `uvicorn` binary.

## Useful commands

- `make test`
- `make compile`
- `make clean`
