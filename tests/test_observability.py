import logging

from app.observability import balance_snapshot, bind_log_context, log_event, sanitize_mapping


def test_sanitize_mapping_redacts_sensitive_values():
    sanitized = sanitize_mapping(
        {
            "apikey": "secret",
            "Authorization": "Bearer abc",
            "token_count": 123,
            "query": "Inception",
        }
    )

    assert sanitized["apikey"] == "***REDACTED***"
    assert sanitized["Authorization"] == "***REDACTED***"
    assert sanitized["token_count"] == "***REDACTED***"
    assert sanitized["query"] == "Inception"


def test_log_event_includes_context(caplog):
    logger = logging.getLogger("tests.observability")

    with caplog.at_level(logging.INFO):
        with bind_log_context(request_id="api-test", channel="api"):
            log_event(logger, logging.INFO, "custom_event", answer=42)

    assert "custom_event" in caplog.text
    assert 'request_id="api-test"' in caplog.text
    assert 'channel="api"' in caplog.text
    assert "answer=42" in caplog.text


class DummyBalanceEntry:
    def __init__(self, usage: str, value: float) -> None:
        self.usage = usage
        self.value = value


class DummyBalance:
    def __init__(self) -> None:
        self.balance = [
            DummyBalanceEntry("tokens", 1234),
            DummyBalanceEntry("images", 12),
        ]


def test_balance_snapshot_returns_simple_mapping():
    assert balance_snapshot(DummyBalance()) == {"images": 12, "tokens": 1234}
