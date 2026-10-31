"""Per-model token pricing for every LLM call's estimated cost.

Prices live in ``configs/llm_pricing.yaml`` (USD per 1,000,000 tokens) and are an
ESTIMATE, not a billing source of truth — edit the YAML to match your provider's
current price sheet. The per-request total feeds the cost guard, the escalation
budget and the answer ``usage`` block, so settings loaded from YAML must price
every configured model (``require_priced``). A call to a model with no entry still
logs its tokens with cost ``unknown`` (a one-time warning names it).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from utils.logging import get_logger

log = get_logger(__name__)

# Repo root = two levels up: src/llm/pricing.py
REPO_ROOT = Path(__file__).resolve().parents[2]

_RATE_KEYS = ("input", "output", "cache_read", "cache_write")
_ROW_KEYS = frozenset({*_RATE_KEYS, "max_priced_prompt_tokens"})

_warned: set[str] = set()


@lru_cache(maxsize=1)
def _table() -> dict[str, dict]:
    path = REPO_ROOT / "configs" / "llm_pricing.yaml"
    if not path.exists():  # pragma: no cover - config is shipped, but degrade safely
        return {}
    with path.open("rt", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    validate_rate_card(data)
    return {str(k): dict(v) for k, v in data["models"].items()}


def model_price(model: str) -> dict | None:
    """Exact match first, then longest-prefix (so dated/suffixed model ids like
    ``gpt-5.4-mini-2026-01`` resolve to the ``gpt-5.4-mini`` entry)."""
    table = _table()
    if model in table:
        return table[model]
    best: str | None = None
    for name in table:
        if model.startswith(name) and (best is None or len(name) > len(best)):
            best = name
    return table[best] if best else None


def cost_usd(model: str, *, prompt_tokens: int = 0, completion_tokens: int = 0,
             cached_tokens: int = 0) -> float | None:
    """Estimated USD cost for one call, or ``None`` if the model isn't priced.

    ``cached_tokens`` is the cached subset of ``prompt_tokens``; it is billed at
    ``cache_read`` (falling back to ``input`` when that rate isn't configured).
    Equivalent to `call_cost` with no cache writes."""
    return call_cost(model, prompt_tokens=prompt_tokens,
                     completion_tokens=completion_tokens, cached_tokens=cached_tokens)


def require_priced(models: Iterable[str]) -> None:
    """Raises unless every model id has a rate-card row (spec llm-model-registry C1).

    Args:
        models: Model ids, resolved as `model_price` resolves them.

    Raises:
        ValueError: Naming every id that has no row.
    """
    missing = sorted({m for m in models if model_price(m) is None})
    if missing:
        raise ValueError(
            f"no rate in configs/llm_pricing.yaml for model(s) {missing}: an unpriced "
            "model adds $0 to the cost guard and the usage totals; add a row for each")


def call_cost(model: str, *, prompt_tokens: int = 0, completion_tokens: int = 0,
              cached_tokens: int = 0, cache_write_tokens: int = 0) -> float | None:
    """Estimated USD cost of one call, with cache reads and writes (spec llm-model-registry C5).

    Args:
        model: The model id.
        prompt_tokens: All prompt tokens, including the cached and cache-written ones.
        completion_tokens: Output tokens.
        cached_tokens: Prompt tokens read from the provider's cache.
        cache_write_tokens: Prompt tokens written to the provider's cache.

    Returns:
        The cost in USD, or None when the model has no rate-card row.

    Raises:
        ValueError: A count is negative, or cached plus written tokens exceed the prompt.
    """
    counts = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
              "cached_tokens": cached_tokens, "cache_write_tokens": cache_write_tokens}
    negative = sorted(k for k, v in counts.items() if v < 0)
    if negative:
        raise ValueError(f"negative token count(s) {negative} for {model}")
    if cached_tokens + cache_write_tokens > prompt_tokens:
        raise ValueError(f"cached ({cached_tokens}) + written ({cache_write_tokens}) tokens "
                         f"exceed the prompt ({prompt_tokens}) for {model}")
    price = model_price(model)
    if not price:
        if model not in _warned:
            _warned.add(model)
            log.warning("no pricing for model %r; logging tokens without cost "
                        "(add it to configs/llm_pricing.yaml)", model)
        return None
    limit = price.get("max_priced_prompt_tokens")
    if limit is not None and prompt_tokens > limit:
        log.warning("%s prompt of %d tokens is above the %d the rate card prices; "
                    "the provider charges more, so this cost is understated",
                    model, prompt_tokens, limit)
    per_in = float(price["input"])
    per_cache = float(price.get("cache_read", per_in))
    per_write = float(price.get("cache_write", per_in))
    uncached = prompt_tokens - cached_tokens - cache_write_tokens
    return (uncached * per_in
            + cached_tokens * per_cache
            + cache_write_tokens * per_write
            + completion_tokens * float(price["output"])) / 1_000_000


def _is_rate(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def validate_rate_card(data: Mapping[str, object]) -> None:
    """Checks a parsed rate card (spec llm-model-registry C8).

    Args:
        data: The parsed ``configs/llm_pricing.yaml``.

    Raises:
        ValueError: The card or one of its rows is malformed.
    """
    if not isinstance(data, Mapping):
        raise ValueError("llm_pricing.yaml must be a mapping with a `models` key")
    extra = sorted(str(k) for k in data if k != "models")
    if extra:
        raise ValueError(f"llm_pricing.yaml: unknown top-level key(s) {extra}")
    models = data.get("models")
    if not isinstance(models, Mapping):
        raise ValueError("llm_pricing.yaml: `models` must map model ids to rate rows")
    for name, row in models.items():
        where = f"llm_pricing.yaml models[{name!r}]"
        if not isinstance(name, str) or not isinstance(row, Mapping):
            raise ValueError(f"{where}: must map a model id to a rate row")
        unknown = sorted(str(k) for k in row if k not in _ROW_KEYS)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown} (allowed: {sorted(_ROW_KEYS)})")
        missing = [k for k in ("input", "output") if k not in row]
        if missing:
            raise ValueError(f"{where}: missing {missing}")
        for key in _RATE_KEYS:
            if key in row and not _is_rate(row[key]):
                raise ValueError(f"{where}.{key} must be a finite number >= 0, got {row[key]!r}")
        if "max_priced_prompt_tokens" in row:
            limit = row["max_priced_prompt_tokens"]
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise ValueError(f"{where}.max_priced_prompt_tokens must be an int > 0, "
                                 f"got {limit!r}")
