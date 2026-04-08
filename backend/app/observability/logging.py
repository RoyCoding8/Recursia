"""Lightweight structured logging helpers for backend observability."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class JsonFormatter(logging.Formatter):
    """Format log records as compact JSON objects."""

    RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc_now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extras: dict[str, Any] = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self.RESERVED and not key.startswith("_")
        }
        if extras:
            payload["context"] = extras

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_structured_logging(level: int = logging.INFO) -> None:
    """Configure root logging with a JSON formatter on stdout."""
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str, **base_context: Any) -> "BoundLogger":
    """Return a contextual logger wrapper."""
    return BoundLogger(logging.getLogger(name), context=dict(base_context))


def log_event(
    logger: logging.Logger,
    event_name: str,
    *,
    level: int = logging.INFO,
    **context: Any,
) -> None:
    """Emit a structured event with context fields."""
    logger.log(level, event_name, extra=context)


class BoundLogger:
    """Small context-binding utility for structured logs."""

    def __init__(
        self, logger: logging.Logger, *, context: dict[str, Any] | None = None
    ):
        self._logger = logger
        self._context = context or {}

    def bind(self, **context: Any) -> "BoundLogger":
        next_context = {**self._context, **context}
        return BoundLogger(self._logger, context=next_context)

    def log(
        self, event_name: str, *, level: int = logging.INFO, **context: Any
    ) -> None:
        merged = {**self._context, **context}
        log_event(self._logger, event_name, level=level, **merged)

    def info(self, event_name: str, **context: Any) -> None:
        self.log(event_name, level=logging.INFO, **context)

    def warning(self, event_name: str, **context: Any) -> None:
        self.log(event_name, level=logging.WARNING, **context)

    def error(self, event_name: str, **context: Any) -> None:
        self.log(event_name, level=logging.ERROR, **context)

    def exception(self, event_name: str, **context: Any) -> None:
        merged = {**self._context, **context}
        self._logger.exception(event_name, extra=merged)


__all__ = [
    "BoundLogger",
    "JsonFormatter",
    "configure_structured_logging",
    "get_logger",
    "log_event",
]
