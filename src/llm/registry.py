"""Versioned, provider/model-aware prompt registry.

Prompts live under the top-level ``prompts/`` tree:

    prompts/
      system/<role>.md                 # promoted system-role strings
      tasks/<task>/base.md             # provider/model-agnostic user template
      tasks/<task>/base.meta.yaml      # variant metadata (system role, status, …)
      tasks/<task>/variants/           # (future) provider+model-series pins
        <provider>.<series>.v<N>.md
        <provider>.<series>.v<N>.meta.yaml

``resolve(task, provider, model, pin=)`` picks the best variant for the active
model and falls back along a chain so we never have to author N×M prompts:

    pin  ->  (task, provider, series, latest active)  ->  (task, provider, base)
         ->  (task, base)

Each resolved prompt carries a ``variant_id`` and a ``content_hash`` (sha256 of
system + user template) so every generation is attributable to an exact prompt
version in the trace / call log. See
``docs/design/Macro_RAG_LLM_Harness.md`` §4.

Phase 1 ships base variants only; the variant scan is implemented and unit-tested
so dropping a ``variants/*.meta.yaml`` in later just works — no code change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from utils.config import REPO_ROOT
from utils.logging import get_logger

log = get_logger(__name__)

_PROMPTS_DIR = REPO_ROOT / "prompts"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ResolvedPrompt:
    """A fully-resolved prompt: the system string, the user template, and the
    provenance (variant id + content hash) to stamp into the trace."""
    task: str
    variant_id: str
    content_hash: str
    system: str
    user_template: str
    schema: dict | None = None   # Phase 2 populates from tasks/<task>/schema.json
    meta: dict = field(default_factory=dict)

    def render(self, **kwargs) -> str:
        """Render the user template with ``{placeholder}`` substitution. Literal
        braces in the data are safe — only declared keys are substituted (same
        contract as the legacy ``llm.prompts.render``)."""
        out = self.user_template
        for key, value in kwargs.items():
            out = out.replace("{" + key + "}", str(value))
        return out


def _model_series(model: str | None) -> str | None:
    """Coarse family key for variant matching, e.g. ``gpt-5.4-mini`` -> ``gpt-5``,
    ``claude-opus-4-8`` -> ``claude-opus-4``. Variants declare a ``model_series``
    prefix; a variant matches when the model id starts with it, so this is only a
    fallback grouping used when a variant omits an explicit series match."""
    if not model:
        return None
    return model


class PromptRegistry:
    def __init__(self, root: Path = _PROMPTS_DIR):
        self.root = root
        self._cache: dict[tuple, ResolvedPrompt] = {}   # prompts are process-static
        self._pins: dict[str, str] = {}                 # task -> forced variant_id

    # ── variant pinning (regression matrix / config prompts.pins) ────────────
    def set_pins(self, pins: dict[str, str]) -> None:
        """Force a variant_id per task for all subsequent resolves that don't pass
        an explicit ``pin=``. Used by the eval-matrix to run 'old prompt on new
        model' and by ``configs/rag.yaml -> prompts.pins`` to freeze a variant."""
        self._pins = dict(pins or {})

    def clear_pins(self) -> None:
        self._pins = {}

    def get_pins(self) -> dict[str, str]:
        return dict(self._pins)

    # ── system roles ────────────────────────────────────────────────────────
    def _system(self, role: str) -> str:
        # .strip() so a trailing newline in the .md file doesn't alter the system
        # prompt bytes sent to the provider (keeps the Anthropic cache breakpoint
        # and OpenAI system message byte-identical to the pre-migration constant).
        return (self.root / "system" / f"{role}.md").read_text(encoding="utf-8").strip()

    # ── variant selection ───────────────────────────────────────────────────
    def _load_meta(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _variant_candidates(self, task: str) -> list[tuple[Path, dict]]:
        """All (meta_path, meta) for a task's variants/ dir (may be empty)."""
        vdir = self.root / "tasks" / task / "variants"
        if not vdir.is_dir():
            return []
        out = []
        for meta_path in sorted(vdir.glob("*.meta.yaml")):
            out.append((meta_path, self._load_meta(meta_path)))
        return out

    def _pick_variant(self, task: str, provider: str | None,
                      model: str | None, pin: str | None) -> tuple[Path, dict]:
        """Return (body_path, meta) for the chosen variant, following the fallback
        chain. ``body_path`` is the .md sibling of the .meta.yaml."""
        candidates = self._variant_candidates(task)

        # 1) explicit pin by variant_id
        if pin:
            for mpath, meta in candidates:
                if meta.get("variant_id") == pin:
                    return mpath.with_name(mpath.name.replace(".meta.yaml", ".md")), meta
            if pin != "base":
                log.warning("prompt pin '%s' not found for task '%s'; falling back", pin, task)

        # 2) provider + model-series, highest active version
        if provider:
            matches = []
            for mpath, meta in candidates:
                if meta.get("status") != "active":
                    continue
                if meta.get("provider") != provider:
                    continue
                series = meta.get("model_series")
                if series and model and not model.startswith(series):
                    continue
                if series and not model:
                    continue
                matches.append((mpath, meta))
            if matches:
                matches.sort(key=lambda mm: int(mm[1].get("version", 0)), reverse=True)
                mpath, meta = matches[0]
                return mpath.with_name(mpath.name.replace(".meta.yaml", ".md")), meta

        # 3) base
        base_meta = self.root / "tasks" / task / "base.meta.yaml"
        return (self.root / "tasks" / task / "base.md"), self._load_meta(base_meta)

    # ── public API ──────────────────────────────────────────────────────────
    def resolve(self, task: str, *, provider: str | None = None,
                model: str | None = None, pin: str | None = None) -> ResolvedPrompt:
        # an explicit pin= wins; else a global pin set via set_pins() applies.
        effective_pin = pin if pin is not None else self._pins.get(task)
        key = (task, provider, model, effective_pin)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        body_path, meta = self._pick_variant(task, provider, model, effective_pin)
        user_template = body_path.read_text(encoding="utf-8")
        system = self._system(meta.get("system", "strict-json"))
        rp = ResolvedPrompt(
            task=task,
            variant_id=str(meta.get("variant_id", "base")),
            content_hash=_hash(system + "\n" + user_template),
            system=system,
            user_template=user_template,
            schema=self._schema(task),
            meta=meta,
        )
        self._cache[key] = rp
        return rp

    def _schema(self, task: str) -> dict | None:
        """The task's output JSON Schema (``tasks/<task>/schema.json``), shared
        across variants. Returns None when absent (validation is then skipped)."""
        path = self.root / "tasks" / task / "schema.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_registry() -> PromptRegistry:
    return PromptRegistry()
