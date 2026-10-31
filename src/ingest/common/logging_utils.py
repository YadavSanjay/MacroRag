"""Lightweight logging setup for the ingestion module.

Honours the ``LOG_LEVEL`` env var (set in .env). Idempotent: repeated calls
won't stack duplicate handlers.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every request URL at INFO, and the BEA/EIA/Census APIs take their
    # key as a query parameter, so INFO would leak API keys (matches the httpx cap
    # in configs/logging.yaml).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
