"""Minimal SDMX-JSON 2.0 client/parser (Ingestion Design §7.1).

The IMF SDMX 3.0 REST API (``api.imf.org/external/sdmx/3.0``) returns SDMX-JSON
where keys are **positional** — the §7.6 classic gotcha. Rather than hardcode
dimension positions, this parser resolves them from the structure block carried
*inline in every data response*, so the positional series key ``0:0:0`` is
mapped back to named dimensions (COUNTRY/INDICATOR/FREQUENCY) using that
response's own DSD. The caller supplies the dimension *key string* per dataflow
(curated in config) in the order the DSD mandates.

Deliberately dependency-free (no ``sdmx1``): the response is plain JSON and the
flattening is small, which keeps Tier 4 self-contained and testable offline.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from .logging_utils import get_logger

log = get_logger(__name__)

SDMX_JSON_ACCEPT = "application/vnd.sdmx.data+json;version=2.0.0"


def build_data_url(base: str, agency: str, dataflow: str, version: str, key: str,
                   *, start: str | None = None, end: str | None = None) -> str:
    """Construct an SDMX 3.0 data URL.

    ``key`` is the dot-separated dimension value string in DSD order (e.g.
    ``USA+CHN.NGDP_RPCH.A`` for IMF.RES WEO = COUNTRY.INDICATOR.FREQUENCY).
    """
    url = f"{base.rstrip('/')}/data/dataflow/{agency}/{dataflow}/{version}/{key}"
    params = {}
    if start:
        params["startPeriod"] = start
    if end:
        params["endPeriod"] = end
    return url + ("?" + urlencode(params) if params else "")


def _value_id(v: dict) -> str | None:
    """Coded dimensions expose ``id``; time/primitive values expose ``value``."""
    return v.get("id", v.get("value"))


def parse_sdmx_json(payload: dict) -> tuple[list[dict], str | None]:
    """Flatten an SDMX-JSON 2.0 data response to rows. Returns ``(rows, version)``.

    Each row carries the named dimension values (resolved positionally from the
    inline structure), the period, the observation value, and any per-observation
    attribute values present.
    """
    data = (payload or {}).get("data") or {}
    structures = data.get("structures") or []
    if not structures:
        return [], None
    st = structures[0]

    series_dims = st.get("dimensions", {}).get("series", []) or []
    obs_dims = st.get("dimensions", {}).get("observation", []) or []
    obs_attr_defs = st.get("attributes", {}).get("observation", []) or []
    version = _resolve_version(st)

    rows: list[dict] = []
    for ds in data.get("dataSets", []) or []:
        for skey, sval in (ds.get("series") or {}).items():
            dim_map = _decode_series_key(skey, series_dims)
            for okey, ovals in (sval.get("observations") or {}).items():
                row = dict(dim_map)
                row["period"] = _decode_obs_key(okey, obs_dims)
                row["value"] = _to_float(ovals[0] if ovals else None)
                attrs = _decode_obs_attrs(ovals, obs_attr_defs)
                if attrs:
                    row["obs_attributes"] = attrs
                row["series_key"] = skey
                rows.append(row)
    rows.sort(key=lambda r: (str(r.get("series_key")), str(r.get("period"))))
    return rows, version


def _decode_series_key(skey: str, series_dims: list[dict]) -> dict:
    out: dict = {}
    parts = skey.split(":")
    for pos, dim in enumerate(series_dims):
        vals = dim.get("values", [])
        if pos < len(parts):
            try:
                idx = int(parts[pos])
            except ValueError:
                continue
            if 0 <= idx < len(vals):
                out[dim.get("id", f"DIM{pos}")] = _value_id(vals[idx])
    return out


def _decode_obs_key(okey: str, obs_dims: list[dict]) -> str | None:
    if not obs_dims:
        return okey
    vals = obs_dims[0].get("values", [])
    try:
        idx = int(okey.split(":")[0])
    except ValueError:
        return okey
    return _value_id(vals[idx]) if 0 <= idx < len(vals) else okey


def _decode_obs_attrs(ovals: list, attr_defs: list[dict]) -> dict:
    """Map observation array positions >0 to their attribute ids (if non-empty)."""
    out: dict = {}
    for i, attr in enumerate(attr_defs, start=1):
        if i < len(ovals) and ovals[i] not in (None, ""):
            vals = attr.get("values", [])
            raw = ovals[i]
            try:
                out[attr.get("id", f"ATTR{i}")] = _value_id(vals[int(raw)])
            except (ValueError, IndexError, TypeError):
                out[attr.get("id", f"ATTR{i}")] = raw
    return out


def _resolve_version(structure: dict) -> str | None:
    """Pull the resolved dataflow version from the structure links (§7.6 #2)."""
    for link in structure.get("links", []) or []:
        urn = link.get("urn", "")
        if "Dataflow=" in urn and "(" in urn:
            return urn.split("(", 1)[1].rstrip(")")
    return None


def _to_float(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
