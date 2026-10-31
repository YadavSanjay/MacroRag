"""Outbound alerting for governance signals (Production Hardening §K).

Drift/regression gates and cost-cap breaches were "logged, never enforced" and
alerts were UI-only. This sends them out-of-band: a Slack-compatible incoming
webhook (``{"text": ...}`` — also works for Teams/PagerDuty-style endpoints), or,
when enabled without a webhook, a WARNING log so the signal is never dropped.

Selected by ``AlertingSettings``; ``build_alerter`` returns a no-op when disabled.
The HTTP send is injectable so tests never touch the network.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Optional

from .logging import get_logger

log = get_logger(__name__)


class Alerter(ABC):
    @abstractmethod
    def send(self, message: str, *, level: str = "warning", **context) -> bool:
        """Emit an alert. ``context`` key/values are appended for readability.
        Returns True if delivered (or logged), False on delivery failure."""


class NullAlerter(Alerter):
    """Disabled — alerting off. Never sends."""

    def send(self, message: str, *, level: str = "warning", **context) -> bool:
        return False


def _format(message: str, level: str, context: dict) -> str:
    tag = f"[{level.upper()}] MacroRAG"
    if context:
        extra = " ".join(f"{k}={v}" for k, v in context.items())
        return f"{tag}: {message} ({extra})"
    return f"{tag}: {message}"


class LogAlerter(Alerter):
    """Enabled but no webhook configured — emit at WARNING so the signal is visible
    in logs/observability even without an external destination."""

    def send(self, message: str, *, level: str = "warning", **context) -> bool:
        log.warning("ALERT %s", _format(message, level, context))
        return True


class WebhookAlerter(Alerter):
    """POST a Slack-compatible ``{"text": ...}`` payload to an incoming webhook.
    ``poster`` is injectable ``(url, payload, timeout) -> status_ok`` so tests don't
    hit the network; the default uses ``urllib`` (no extra dependency)."""

    def __init__(self, webhook_url: str, *, timeout_s: float = 5.0,
                 poster: Callable[[str, dict, float], bool] | None = None):
        self.url = webhook_url
        self.timeout = timeout_s
        self._poster = poster or _urllib_post

    def send(self, message: str, *, level: str = "warning", **context) -> bool:
        text = _format(message, level, context)
        try:
            ok = self._poster(self.url, {"text": text}, self.timeout)
            if not ok:
                log.warning("alert webhook returned non-2xx; message was: %s", text)
            return ok
        except Exception as exc:  # never let alerting break the caller
            log.warning("alert webhook failed (%s); message was: %s", exc, text)
            return False


def _urllib_post(url: str, payload: dict, timeout: float) -> bool:
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - operator-configured URL
        return 200 <= resp.status < 300


def build_alerter(settings) -> Alerter:
    """Build the alerter from ``settings.alerting`` (an AlertingSettings)."""
    a = settings.alerting if hasattr(settings, "alerting") else settings
    if not a.enabled:
        return NullAlerter()
    if a.webhook_url:
        return WebhookAlerter(a.webhook_url, timeout_s=a.timeout_s)
    return LogAlerter()
