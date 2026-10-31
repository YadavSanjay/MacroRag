"""Minimal Prometheus-compatible metrics registry (Production Hardening §J).

The admin metrics roll-up re-parses ``queries.jsonl`` on every call and derives
latency from summed trace timings — neither scales, and neither is a real request
latency. This is a tiny in-process registry that renders the Prometheus text
exposition format for a ``/metrics`` endpoint: counters + histograms incremented
in-band (request middleware, LLM accounting). Per-process is exactly what
Prometheus expects — it scrapes each replica and aggregates server-side, so no
shared/Redis state is needed here.

No external dependency (avoids pulling ``prometheus_client`` into the image and
keeps tests fake-free). Thread-safe; safe to call from request threads.
"""

from __future__ import annotations

import threading
from typing import Optional

_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
                    1.0, 2.5, 5.0, 10.0)


def _esc_label(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_labels(labelnames: tuple, values: tuple) -> str:
    if not labelnames:
        return ""
    inner = ",".join(f'{n}="{_esc_label(v)}"' for n, v in zip(labelnames, values))
    return "{" + inner + "}"


def _num(x: float) -> str:
    # Prometheus wants a plain number; render ints without a trailing .0
    if x == int(x):
        return str(int(x))
    return repr(x)


class Counter:
    def __init__(self, name: str, help: str, labelnames: tuple = ()):
        self.name = name
        self.help = help
        self.labelnames = tuple(labelnames)
        self._vals: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def _key(self, labels: dict) -> tuple:
        return tuple(str(labels.get(n, "")) for n in self.labelnames)

    def inc(self, amount: float = 1.0, **labels) -> None:
        key = self._key(labels)
        with self._lock:
            self._vals[key] = self._vals.get(key, 0.0) + amount

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            items = list(self._vals.items())
        if not items and not self.labelnames:
            items = [((), 0.0)]      # an unlabeled counter shows a 0 baseline
        for key, val in items:
            out.append(f"{self.name}{_fmt_labels(self.labelnames, key)} {_num(val)}")
        return out


class Histogram:
    def __init__(self, name: str, help: str, labelnames: tuple = (),
                 buckets: tuple = _DEFAULT_BUCKETS):
        self.name = name
        self.help = help
        self.labelnames = tuple(labelnames)
        self.buckets = tuple(buckets)
        # per label-set: [bucket_counts..., sum, count]
        self._data: dict[tuple, list] = {}
        self._lock = threading.Lock()

    def _key(self, labels: dict) -> tuple:
        return tuple(str(labels.get(n, "")) for n in self.labelnames)

    def observe(self, value: float, **labels) -> None:
        key = self._key(labels)
        with self._lock:
            rec = self._data.get(key)
            if rec is None:
                rec = [[0] * len(self.buckets), 0.0, 0]
                self._data[key] = rec
            counts, _sum, _count = rec
            for i, edge in enumerate(self.buckets):
                if value <= edge:
                    counts[i] += 1
            rec[1] = _sum + value
            rec[2] = _count + 1

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        le_labels = self.labelnames + ("le",)
        with self._lock:
            items = list(self._data.items())
        for key, (counts, total, count) in items:
            # counts[i] is already the cumulative "observations <= buckets[i]"
            for edge, c in zip(self.buckets, counts):
                out.append(f"{self.name}_bucket"
                           f"{_fmt_labels(le_labels, key + (repr(edge),))} {_num(c)}")
            out.append(f"{self.name}_bucket{_fmt_labels(le_labels, key + ('+Inf',))} {_num(count)}")
            out.append(f"{self.name}_sum{_fmt_labels(self.labelnames, key)} {_num(total)}")
            out.append(f"{self.name}_count{_fmt_labels(self.labelnames, key)} {_num(count)}")
        return out


class Registry:
    def __init__(self):
        self._metrics: list = []

    def register(self, metric):
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for m in self._metrics:
            lines.extend(m.render())
        return "\n".join(lines) + "\n"


# ── default registry + predefined metrics ────────────────────────────────────

REGISTRY = Registry()

http_requests_total = REGISTRY.register(Counter(
    "macrorag_http_requests_total", "HTTP requests by method/route/status",
    labelnames=("method", "route", "status")))
http_request_duration_seconds = REGISTRY.register(Histogram(
    "macrorag_http_request_duration_seconds", "HTTP request wall-clock latency",
    labelnames=("method", "route")))
llm_calls_total = REGISTRY.register(Counter(
    "macrorag_llm_calls_total", "LLM completions by provider/model/purpose",
    labelnames=("provider", "model", "purpose")))
llm_tokens_total = REGISTRY.register(Counter(
    "macrorag_llm_tokens_total", "LLM tokens by provider/model/kind",
    labelnames=("provider", "model", "kind")))
llm_cost_usd_total = REGISTRY.register(Counter(
    "macrorag_llm_cost_usd_total", "Estimated LLM spend (USD) by provider/model",
    labelnames=("provider", "model")))
schema_results_total = REGISTRY.register(Counter(
    "macrorag_schema_results_total", "Structured-output outcomes by task/model/outcome",
    labelnames=("task", "model", "outcome")))
prompt_injection_suspected_total = REGISTRY.register(Counter(
    "macrorag_prompt_injection_suspected_total",
    "Suspected prompt-injection markers detected in untrusted input, by source",
    labelnames=("source",)))


def observe_request(method: str, route: str, status: int, duration_s: float) -> None:
    """Record one HTTP request (called from the API middleware)."""
    http_requests_total.inc(method=method, route=route, status=str(status))
    http_request_duration_seconds.observe(duration_s, method=method, route=route)


def record_llm_call(provider: str, model: str, purpose: str | None,
                    prompt_tokens: int, completion_tokens: int,
                    cost_usd: float | None) -> None:
    """Record one LLM call's counters (called from usage.record_call; best-effort)."""
    llm_calls_total.inc(provider=provider, model=model, purpose=purpose or "-")
    if prompt_tokens:
        llm_tokens_total.inc(prompt_tokens, provider=provider, model=model, kind="prompt")
    if completion_tokens:
        llm_tokens_total.inc(completion_tokens, provider=provider, model=model,
                             kind="completion")
    if cost_usd:
        llm_cost_usd_total.inc(cost_usd, provider=provider, model=model)


def record_schema_result(task: str, model: str, outcome: str) -> None:
    """Record one structured-output schema outcome (called from schema_health)."""
    schema_results_total.inc(task=task, model=model, outcome=outcome)


def record_injection_suspected(source: str) -> None:
    """Record one suspected prompt-injection detection (called from sanitize)."""
    prompt_injection_suspected_total.inc(source=source)


def render_metrics() -> str:
    """The Prometheus text exposition for all registered metrics."""
    return REGISTRY.render()
