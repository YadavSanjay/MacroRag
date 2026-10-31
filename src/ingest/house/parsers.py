"""Tier 5 parsing helpers (Ingestion Design §8.6).

House pages carry heavy legal/distribution boilerplate (§8.6 #7) and the
analytically-valuable bits are the body prose + chart captions + speaker
attribution (§8.6 #5, #6). These helpers strip the boilerplate, convert
WordPress/HTML bodies to text, and pull MS transcript + speaker structure.
"""

from __future__ import annotations

import re
from typing import Optional

# ── §8.6 #7 disclaimer / boilerplate stripping ───────────────────────────────

# Cut everything from the first disclaimer marker onward (markers reliably sit
# at the tail of house pages/PDFs after one golden-doc inspection).
_DISCLAIMER_MARKERS = [
    r"This material is intended for information",
    r"This material is provided for",
    r"The opinions expressed are as of",
    r"Past performance is (?:not|no guarantee)",
    r"This information in no way constitutes",
    r"IMPORTANT INFORMATION",
    r"Important Disclosures",
    r"Index performance is shown for illustrative",
    r"forward-looking statements",
    r"investment recommendation or advice",
    r"©\s?\d{4}.{0,40}All rights reserved",
]
_DISCLAIMER_RE = re.compile("|".join(_DISCLAIMER_MARKERS), re.IGNORECASE)


def strip_house_boilerplate(text: str) -> str:
    if not text:
        return ""
    m = _DISCLAIMER_RE.search(text)
    # Only trim if the marker is past the halfway point (avoid cutting a body
    # that legitimately quotes "forward-looking statements" near the top).
    if m and m.start() > len(text) * 0.5:
        text = text[: m.start()]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── HTML body -> text (WordPress content.rendered, etc.) ─────────────────────


def html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup.find_all(["script", "style", "figure", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── §8.6 #5 speaker / transcript extraction (MS Thoughts on the Market) ──────

# Speaker prefixes are "Firstname Lastname:" at line start. Use [ \t] (not \s)
# between names so the match never spans a newline (which would glue a trailing
# heading like "Transcript" onto the following speaker name).
_SPEAKER_LINE = re.compile(r"^([A-Z][a-z]+(?:[ \t]+[A-Z][a-z.'-]+){1,2}):",
                           re.MULTILINE)


def extract_speakers(text: str) -> list[str]:
    """Distinct speaker names from ``Name Name:`` transcript prefixes."""
    seen: list[str] = []
    for name in _SPEAKER_LINE.findall(text or ""):
        n = name.strip()
        if n and n not in seen:
            seen.append(n)
    return seen


def extract_transcript(body: str, marker: str = "Transcript") -> str:
    """Return the transcript portion of an MS episode body, if delimited.

    Uses the *last* marker occurrence — the page often has an earlier nav/anchor
    "Transcript" link before the real transcript heading.
    """
    if not body:
        return ""
    matches = list(re.finditer(rf"-*\s*{re.escape(marker)}\s*-*", body, re.IGNORECASE))
    return body[matches[-1].end():].strip() if matches else body


# ── attribution helpers ──────────────────────────────────────────────────────


def slug_from_url(url: str) -> str | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail or None
