from types import SimpleNamespace

import pytest
from telegram.error import Conflict, NetworkError

import app.bootstrap as bootstrap
import bot.telegram_bot as telegram_bot
from app.config import Settings
from app.models.schemas import MovieCandidate


def test_settings_placeholder_helpers():
    placeholder_settings = Settings(
        telegram_token="PASTE_TELEGRAM_TOKEN_HERE",
        gigachat_credentials="PASTE_GIGACHAT_AUTH_KEY_HERE",
        omdb_api_key="PASTE_OMDB_API_KEY_HERE",
        tmdb_api_token="PASTE_TMDB_API_TOKEN_HERE",
    )

    assert not placeholder_settings.has_telegram_token()
    assert not placeholder_settings.has_gigachat_credentials()
    assert not placeholder_settings.has_omdb_api_key()
    assert not placeholder_settings.has_tmdb_api_token()


def test_build_pipeline_skips_placeholder_source_adapters(monkeypatch):
    fake_settings = SimpleNamespace(
        has_omdb_api_key=lambda: False,
        has_tmdb_api_token=lambda: False,
        omdb_api_key="PASTE_OMDB_API_KEY_HERE",
        tmdb_api_token="PASTE_TMDB_API_TOKEN_HERE",
        cache_enabled=False,
        cache_db_path="unused.db",
        cache_ttl_seconds=60,
    )
    monkeypatch.setattr(bootstrap, "settings", fake_settings)

    pipeline = bootstrap.build_pipeline()

    adapter_names = [adapter.name for adapter in pipeline.source_aggregator.adapters]
    assert adapter_names == ["wikipedia"]


class DummyApp:
    def __init__(self) -> None:
        self.handlers = []
        self.error_handlers = []

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler) -> None:
        self.error_handlers.append(handler)

    def run_polling(self) -> None:
        raise NetworkError("dns failed")


class DummyBuilder:
    def __init__(self) -> None:
        self.token_value = None

    def token(self, token: str) -> "DummyBuilder":
        self.token_value = token
        return self

    def build(self) -> DummyApp:
        return DummyApp()


class DummyApplicationFactory:
    @staticmethod
    def builder() -> DummyBuilder:
        return DummyBuilder()


def test_run_bot_exits_cleanly_on_network_error(monkeypatch, caplog):
    monkeypatch.setattr(
        telegram_bot,
        "settings",
        SimpleNamespace(
            telegram_token="test-token",
            has_telegram_token=lambda: True,
        ),
    )
    monkeypatch.setattr(telegram_bot, "Application", DummyApplicationFactory)
    monkeypatch.setattr(telegram_bot, "build_pipeline", lambda: object())
    monkeypatch.setattr(telegram_bot, "pipeline", None)

    with pytest.raises(SystemExit) as exc:
        telegram_bot.run_bot()

    assert exc.value.code == 1
    assert "could not reach Telegram" in caplog.text


class DummyApplication:
    def __init__(self) -> None:
        self.stop_called = False

    def stop_running(self) -> None:
        self.stop_called = True


@pytest.mark.asyncio
async def test_on_error_stops_bot_on_conflict(caplog):
    application = DummyApplication()
    context = SimpleNamespace(
        error=Conflict("already polling elsewhere"),
        application=application,
    )

    with caplog.at_level("ERROR"):
        await telegram_bot.on_error(None, context)

    assert application.stop_called is True
    assert "telegram_polling_conflict" in caplog.text


def test_selection_state_round_trip_and_clear():
    context = SimpleNamespace(user_data={})
    selection_id = telegram_bot._start_selection_state(context)
    candidates = [
        MovieCandidate(movie_id="tt1375666", title="Inception", year=2010, confidence=0.99),
        MovieCandidate(movie_id="tt0816692", title="Interstellar", year=2014, confidence=0.88),
    ]

    telegram_bot._set_candidate_options(context, selection_id, candidates)
    option = telegram_bot._get_candidate_option(context, selection_id, 0)

    assert option is not None
    assert option["resolved_title"] == "Inception 2010"

    telegram_bot._set_selected_movie(context, selection_id, title="Inception", year=2010)
    assert telegram_bot._selected_movie(context)["resolved_title"] == "Inception 2010"

    assert telegram_bot._set_watched_mode(context, selection_id, True) is True
    assert telegram_bot._watched_mode(context) is True

    telegram_bot._clear_selection_state(context)
    assert telegram_bot._selection_state(context) is None
