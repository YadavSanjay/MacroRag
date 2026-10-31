"""C7: a prompt longer than a row's max_priced_prompt_tokens logs a WARNING naming the model."""

from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HC = [HealthCheck.function_scoped_fixture]
HAIKU = "claude-haiku-5-5"
LIMIT = 100_000            # spec Notes: Haiku 5.5 short-prompt rate applies up to 100K
# Committed rows without max_priced_prompt_tokens (spec: only claude-haiku-5-5 sets it).
OTHER_ROWS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-4-8", "claude-opus-4-7",
              "claude-sonnet-4-6", "claude-haiku-4-5", "gpt-5.4", "gpt-5.4-mini",
              "gpt-5.4-nano", "gpt-6-sol", "gpt-6-luna")


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def capture_logger(name):
    """Collect every record on logger ``name`` (handler attached directly, so the capture
    does not depend on propagation or the root logging configuration)."""
    logger = logging.getLogger(name)
    handler = _ListHandler()
    old_level, old_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.disabled = old_disabled


def _warnings_naming(model, **kw):
    """(cost, number of WARNING records on llm.pricing whose message contains model)."""
    from llm.pricing import call_cost

    with capture_logger("llm.pricing") as records:
        cost = call_cost(model, **kw)
    hits = [r for r in records
            if r.name == "llm.pricing" and r.levelno == logging.WARNING
            and model in r.getMessage()]
    return cost, len(hits)


@pytest.mark.criterion("C7")
def test_haiku_5_5_warns_above_100k_only():
    # oracle: spec C7 + Notes (Haiku 5.5 charges a higher rate above 100K prompt tokens):
    # 100001 warns, 100000 does not.
    _, above = _warnings_naming(HAIKU, prompt_tokens=LIMIT + 1)
    _, at = _warnings_naming(HAIKU, prompt_tokens=LIMIT)
    assert above >= 1
    assert at == 0


@pytest.mark.criterion("C7")
def test_long_prompt_cost_is_unchanged_short_rate():
    # oracle: spec Requirement "The cost itself is unchanged" + Notes short-prompt rates:
    #   haiku-5-5 P=200000 O=1000: (200000*0.10 + 1000*0.50)/1e6 = (20000+500)/1e6 = 0.0205
    cost, n = _warnings_naming(HAIKU, prompt_tokens=200_000, completion_tokens=1000)
    assert n >= 1
    # tolerance: abs=1e-12 USD, spec C5 precision
    assert cost == pytest.approx(0.0205, abs=1e-12)


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("model", OTHER_ROWS)
def test_rows_without_limit_never_warn(model):
    # oracle: spec C7 — a row without max_priced_prompt_tokens logs no such WARNING at any
    # prompt length; the haiku-5-5 row (which has one) does, at the same length.
    _, n = _warnings_naming(model, prompt_tokens=10_000_000)
    assert n == 0
    _, h = _warnings_naming(HAIKU, prompt_tokens=10_000_000)
    assert h >= 1


@pytest.mark.criterion("C7")
@settings(max_examples=60, deadline=None, suppress_health_check=_HC)
@given(p=st.integers(0, 5_000_000), model=st.sampled_from(OTHER_ROWS))
def test_property_warns_iff_haiku_prompt_exceeds_limit(p, model):
    # oracle: spec C7 — haiku-5-5 warns iff prompt_tokens > 100000; other rows never warn.
    _, n = _warnings_naming(HAIKU, prompt_tokens=p)
    assert (n >= 1) == (p > LIMIT)
    _, m = _warnings_naming(model, prompt_tokens=p)
    assert m == 0
    _, fixed = _warnings_naming(HAIKU, prompt_tokens=LIMIT + 1)
    assert fixed >= 1


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_warning_tracks_the_boundary_not_the_other_counts():
    # oracle: spec C7. Relation: at a fixed prompt length, completion and cache counts do
    # not change whether the warning fires; only crossing 100000 prompt tokens does.
    for kw in (dict(), dict(completion_tokens=50_000), dict(cached_tokens=90_000),
               dict(cache_write_tokens=60_000)):
        _, at = _warnings_naming(HAIKU, prompt_tokens=LIMIT, **kw)
        _, above = _warnings_naming(HAIKU, prompt_tokens=LIMIT + 1, **kw)
        assert at == 0
        assert above >= 1
