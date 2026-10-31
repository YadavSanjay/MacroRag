"""Backward-compat shim over the prompt registry (see :mod:`llm.registry`).

Prompts historically lived in ``prompt/<name>.txt`` loaded by name; they now live
under ``prompts/tasks/<name>/base.md`` behind :class:`PromptRegistry`, which adds
provider/model-aware variants and provenance (variant id + content hash).

``render()`` / ``load_prompt()`` remain as thin wrappers over the *base* user
template so existing callers and tests keep working unchanged. New code should use
``get_registry().resolve(task, provider=…, model=…)`` — it returns the resolved
system prompt, the user template, and the provenance to stamp into the trace.
"""

from __future__ import annotations

from .registry import get_registry


def load_prompt(name: str) -> str:
    """The base user template for a task (byte-identical to the old ``prompt/*.txt``)."""
    return get_registry().resolve(name).user_template


def render(name: str, **kwargs) -> str:
    """Render a task's base user template with ``{placeholder}`` substitution."""
    return get_registry().resolve(name).render(**kwargs)
