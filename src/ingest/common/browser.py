"""Headless-browser fetch (Ingestion Design §2.8, §8.6 #4 — JS-rendered pages).

A shared fallback for sources whose content/list is rendered client-side and so
invisible to polite HTTP (CBO behind DataDome; Goldman Sachs Top of Mind). Uses
Playwright/Chromium, imported lazily so the dependency is optional: if Playwright
or its browser binary is missing, :func:`render` returns ``None`` and callers
degrade gracefully (the same posture as before this capability existed).
"""

from __future__ import annotations

from typing import Optional

from .logging_utils import get_logger

log = get_logger(__name__)

_UNAVAILABLE = False   # set once if Playwright/browser is missing, to stop retrying


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return not _UNAVAILABLE
    except ImportError:
        return False


def render(
    url: str,
    *,
    user_agent: str | None = None,
    timeout_ms: int = 30000,
    settle_ms: int = 2500,
    wait_selector: str | None = None,
) -> str | None:
    """Return the fully-rendered HTML for ``url``, or ``None`` on any failure.

    ``settle_ms`` is a post-load pause to let client-side JS populate; pass
    ``wait_selector`` to instead block until a specific element appears.
    """
    global _UNAVAILABLE
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright not installed; cannot render %s", url)
        _UNAVAILABLE = True
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent=user_agent,
                    viewport={"width": 1366, "height": 900},
                )
                page = ctx.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=timeout_ms)
                    except Exception:
                        pass
                page.wait_for_timeout(settle_ms)
                return page.content()
            finally:
                browser.close()
    except Exception as exc:
        # A missing browser binary surfaces here; flag it so we stop retrying.
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            _UNAVAILABLE = True
            log.warning("playwright browser binary missing (run "
                        "`playwright install chromium`); rendering disabled.")
        else:
            log.warning("playwright render failed for %s: %s", url, exc)
        return None
