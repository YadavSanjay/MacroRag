"""Project logging (mirrors the sister ARag project's setup).

``setup_logging()`` loads ``configs/logging.yaml`` via ``logging.config.dictConfig``:
a human-readable **stderr** console handler and a rotating **JSON** file handler
(``logs/macro_app.log``). The console goes to stderr deliberately — under the
stdio MCP transport stdout is the JSON-RPC channel, so a stray log line there
corrupts the protocol. If the YAML (or PyYAML) is missing it falls back to a
simple stderr ``basicConfig``. ``LOG_LEVEL`` (or the ``level`` arg) overrides the
root level after load.

    from utils.logging import setup_logging, get_logger
    setup_logging()                 # once, at program start (entrypoints)
    log = get_logger(__name__)

A per-query correlation id (``set_query_id`` / ``current_query_id``) is stamped
onto every record by :class:`LogContextFilter`, surfacing as ``qid=`` in the
console line and ``query_id`` in the JSON file logs; it defaults to ``"-"``.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import os
import sys
from pathlib import Path

try:  # PyYAML is a project dep; degrade gracefully if absent
    import yaml
except Exception:  # pragma: no cover - instructive fallback
    yaml = None

# Repo root = two levels up: src/utils/logging.py
REPO_ROOT = Path(__file__).resolve().parents[2]
# Base for relative log-file paths in configs/logging.yaml: $MACRORAG_LOG_DIR if
# set (tests and their subprocesses use a temp dir), else the repo root.
LOG_ROOT = Path(os.environ.get("MACRORAG_LOG_DIR") or REPO_ROOT)

_CONTEXT_NONE = "-"
_FALLBACK_FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_FALLBACK_DATEFMT = "%H:%M:%S"

# Per-query correlation id: set at the top of a request/query, stamped onto every
# log line emitted while it is active (see LogContextFilter).
_query_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "macro_query_id", default=_CONTEXT_NONE)


def set_query_id(value: str | None) -> contextvars.Token:
    """Bind a correlation id for the current context; returns a reset token."""
    return _query_id.set(value or _CONTEXT_NONE)


def reset_query_id(token: contextvars.Token) -> None:
    _query_id.reset(token)


def current_query_id() -> str:
    return _query_id.get()


class LogContextFilter(logging.Filter):
    """Stamp every record with the active query's correlation id (default ``-``),
    so format strings and the JSON formatter can reference ``query_id``."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "query_id", None):
            record.query_id = current_query_id()
        return True


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line (machine-parseable file logs)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "query_id": getattr(record, "query_id", _CONTEXT_NONE),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _config_path() -> Path:
    return REPO_ROOT / "configs" / "logging.yaml"


def _prepare_file_handlers(cfg: dict) -> None:
    """Resolve each file handler's ``filename`` against ``LOG_ROOT`` (the repo root,
    so logs land in the same place regardless of cwd) and ensure the directory
    exists."""
    for handler in (cfg.get("handlers") or {}).values():
        fn = handler.get("filename")
        if not fn:
            continue
        p = Path(fn)
        if not p.is_absolute():
            p = LOG_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        handler["filename"] = str(p)


def setup_logging(level: str | None = None,
                  config_path: str | Path | None = None) -> None:
    """Configure logging from ``configs/logging.yaml`` (dictConfig). Idempotent —
    safe to call once per entrypoint. Falls back to a stderr ``basicConfig`` if the
    config/PyYAML is unavailable. ``level`` or ``$LOG_LEVEL`` override the root level."""
    path = Path(config_path) if config_path else _config_path()
    env_level = level or os.environ.get("LOG_LEVEL")

    if path.exists() and yaml is not None:
        with path.open("rt", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        _prepare_file_handlers(cfg)
        logging.config.dictConfig(cfg)
    else:  # pragma: no cover - exercised only when config/PyYAML missing
        logging.basicConfig(level=(env_level or "INFO").upper(),
                            format=_FALLBACK_FMT, datefmt=_FALLBACK_DATEFMT,
                            stream=sys.stderr)

    if env_level:
        try:
            logging.getLogger().setLevel(getattr(logging, env_level.upper()))
        except Exception:  # pragma: no cover - bad LOG_LEVEL string; keep default
            pass

    # §J: structured JSON on the console stream too (for container log scraping).
    # Off by default (human-readable console); the file handler is already JSON.
    if os.environ.get("MACRORAG_LOG_JSON", "").strip().lower() in {"1", "true", "yes", "on"}:
        for handler in logging.getLogger().handlers:
            # StreamHandler but NOT a FileHandler (RotatingFileHandler subclasses it
            # and is already JSON); swap the console formatter to JSON.
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                    handler, logging.FileHandler):
                handler.setFormatter(JsonFormatter())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
