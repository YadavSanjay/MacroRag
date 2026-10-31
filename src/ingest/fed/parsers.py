"""Fed-specific structured-field extraction (Ingestion Design §4.1, §4.5).

Pure functions over already-extracted body text — no I/O. Each returns a dict of
scalar ``fields`` that get attached to the :class:`ParsedDocument` (and the
single most important ones, like the FOMC target range, are what the stress
engine ultimately retrieves with full citation).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

# ── shared helpers ───────────────────────────────────────────────────────────

_DATE8 = re.compile(r"(\d{8})")
_DATE6 = re.compile(r"(\d{6})")


def _as_date_or_none(value) -> date | None:
    """Coerce an optional datetime to a date (used for RSS pubDates)."""
    return value.date() if value is not None else None


def date_from_url(url: str) -> date | None:
    """Pull a publication date out of a Fed URL slug (``...20260318a.htm``)."""
    m = _DATE8.search(url)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    m = _DATE6.search(url)
    if m:
        try:
            return datetime.strptime(m.group(1) + "01", "%Y%m%d").date()
        except ValueError:
            pass
    return None


_LAST_UPDATE = re.compile(r"Last Update:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def date_from_last_update(html: bytes | str) -> date | None:
    """Parse the ``Last Update: August 03, 2026`` footer on federalreserve.gov pages.

    Args:
        html: Raw page HTML.

    Returns:
        The footer date, or ``None`` if the page has no parseable footer.
    """
    text = html if isinstance(html, str) else html.decode("utf-8", "ignore")
    m = _LAST_UPDATE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return None


# ── FOMC statement (§4.1 a) ──────────────────────────────────────────────────

_RATE_RANGE = re.compile(
    r"target range for the federal funds rate\s+(?:at|to)\s+"
    r"([\d\s\-/]+?)\s+to\s+([\d\s\-/]+?)\s+percent",
    re.IGNORECASE,
)
_RELEASE_TIME = re.compile(r"For release at\s+(\d{1,2}:\d{2}\s*[ap]\.m\.\s*E[SD]T)",
                           re.IGNORECASE)
_VOTE_TALLY = re.compile(r"by a\s+(\d+)\s*[–—-]\s*(\d+)\s+vote", re.IGNORECASE)
_VOTING_FOR = re.compile(
    r"Voting for the(?:\s+monetary policy)?\s+action\s+were[:,]?\s+(.+?)(?:\.\s|\.$)",
    re.IGNORECASE | re.DOTALL,
)
_VOTING_AGAINST = re.compile(
    r"Voting against (?:this|the) action (?:were|was)[:,]?\s+(.+?)(?:\.\s|\.$)",
    re.IGNORECASE | re.DOTALL,
)
_FORWARD_GUIDANCE = re.compile(
    r"(In (?:considering|determining) the (?:extent|timing).+?)(?:\n\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_fomc_statement(text: str) -> dict:
    """Extract the headline data points from an FOMC statement body."""
    fields: dict = {}

    m = _RATE_RANGE.search(text)
    if m:
        lo, hi = m.group(1).strip(), m.group(2).strip()
        fields["target_rate_range"] = f"{lo} to {hi} percent"
        fields["target_rate_low"] = lo
        fields["target_rate_high"] = hi

    if (m := _RELEASE_TIME.search(text)):
        fields["release_time"] = m.group(1).strip()

    if (m := _VOTE_TALLY.search(text)):
        fields["vote_for"] = int(m.group(1))
        fields["vote_against"] = int(m.group(2))

    if (m := _VOTING_FOR.search(text)):
        fields["voters"] = _split_names(m.group(1))

    if (m := _VOTING_AGAINST.search(text)):
        fields["dissenters"] = _split_names(m.group(1))
    else:
        fields["dissenters"] = []

    if (m := _FORWARD_GUIDANCE.search(text)):
        fields["forward_guidance_language"] = _collapse(m.group(1))

    return fields


def _split_names(blob: str) -> list[str]:
    blob = _collapse(blob)
    blob = re.sub(r"\band\b", ",", blob)
    return [n.strip(" .;") for n in blob.split(",") if n.strip(" .;")]


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ── Fed speeches (§4.5) ──────────────────────────────────────────────────────

_SPEAKER = re.compile(
    r"^\s*(Chair(?:\s+Pro\s+Tempore)?|Vice\s+Chair(?:\s+for\s+Supervision)?|"
    r"Governor|President)\s+([A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+)*)",
)


def parse_speech_title(title: str) -> dict:
    """Derive speaker / role / topic from a speeches-feed title.

    The live feed uses a ``"Speaker, Topic"`` format (e.g.
    ``"Jefferson, Global Economic Developments and the U.S. Economy"``); some
    items instead carry an explicit role prefix (``"Chair Warsh: ..."``). Both
    shapes are handled.
    """
    fields: dict = {}
    title = title.strip()

    # Shape 1: explicit role prefix ("Chair Warsh: ...").
    if (m := _SPEAKER.match(title)):
        fields["speaker_role"] = _collapse(m.group(1))
        fields["speaker"] = _collapse(m.group(2))
        rest = title[m.end():].lstrip(" :–—-")
        if rest:
            fields["speech_topic"] = rest.strip()
        return fields

    # Shape 2: "Speaker, Topic" — speaker is the token before the first comma.
    if "," in title:
        speaker, _, topic = title.partition(",")
        if 0 < len(speaker.split()) <= 4:
            fields["speaker"] = speaker.strip()
            if topic.strip():
                fields["speech_topic"] = topic.strip()
    return fields


def speech_priority(title: str, role: str | None,
                    high_keywords: list[str], high_speakers: list[str]) -> int:
    """Priority filter (1=highest) from §4.5 — tier-1/2/3 speech weighting."""
    title_l = title.lower()
    if role and any(s.lower() in role.lower() for s in high_speakers):
        return 1
    if any(k.lower() in title_l for k in high_keywords):
        return 1
    if role and "governor" in role.lower():
        return 2
    return 3


# ── PDF tables -> time-series rows (SEP / FSR / DFAST / SLOOS) ────────────────


def tables_to_timeseries(tables: list[list[list[str]]], *, source: str,
                         page: int) -> list[dict]:
    """Flatten extracted PDF tables into provenance-tagged time-series rows.

    Intentionally schema-light: it preserves the raw grid + a source tag so the
    downstream time-series loader can map specific named scenarios (e.g.
    ``DFAST_2026_severely_adverse``) without this stage hard-coding each layout.
    """
    rows: list[dict] = []
    for t_idx, table in enumerate(tables):
        if not table:
            continue
        header = [c for c in table[0]] if table else []
        for r_idx, row in enumerate(table[1:], start=1):
            rows.append({
                "source": source,
                "page": page,
                "table_index": t_idx,
                "row_index": r_idx,
                "header": header,
                "cells": row,
            })
    return rows
