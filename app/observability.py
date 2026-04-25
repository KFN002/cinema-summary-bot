from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping


_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_channel_var: contextvars.ContextVar[str] = contextvars.ContextVar("channel", default="-")

_REDACTED = "***REDACTED***"
_SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "token", "secret", "credential")


def configure_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
        return
    root_logger.setLevel(level)


def new_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@contextmanager
def bind_log_context(*, request_id: str | None = None, channel: str | None = None) -> Iterator[None]:
    request_token = None
    channel_token = None
    try:
        if request_id is not None:
            request_token = _request_id_var.set(request_id)
        if channel is not None:
            channel_token = _channel_var.set(channel)
        yield
    finally:
        if request_token is not None:
            _request_id_var.reset(request_token)
        if channel_token is not None:
            _channel_var.reset(channel_token)


def current_request_id() -> str:
    return _request_id_var.get()


def current_channel() -> str:
    return _channel_var.get()


def elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def sanitize_mapping(values: Mapping[str, Any] | None) -> dict[str, Any]:
    if not values:
        return {}

    sanitized: dict[str, Any] = {}
    for key, value in values.items():
        normalized_key = str(key).lower()
        if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
            sanitized[str(key)] = _REDACTED
        else:
            sanitized[str(key)] = value
    return sanitized


def balance_snapshot(balance: Any) -> dict[str, Any]:
    entries = getattr(balance, "balance", None) or []
    snapshot: dict[str, Any] = {}
    for entry in entries:
        usage_name = getattr(entry, "usage", None)
        usage_value = getattr(entry, "value", None)
        if usage_name is None:
            continue
        snapshot[str(usage_name)] = usage_value
    return snapshot


def event_message(event: str, **fields: Any) -> str:
    payload = {
        "request_id": current_request_id(),
        "channel": current_channel(),
        **{key: value for key, value in fields.items() if value is not None},
    }
    return _format_event(event, payload)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event_message(event, **fields))


def log_exception(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.exception(event_message(event, **fields))


def _format_event(event: str, payload: Mapping[str, Any]) -> str:
    parts = [event]
    for key, value in payload.items():
        parts.append(f"{key}={_serialize_value(value)}")
    return " ".join(parts)


def _serialize_value(value: Any) -> str:
    if isinstance(value, set):
        value = sorted(value)
    elif isinstance(value, tuple):
        value = list(value)
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=True)
