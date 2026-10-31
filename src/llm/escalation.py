"""Quality-signal-driven model escalation — the decision half (design doc §5).

When a cheap-model pass deteriorates, the pipeline retries the same generation on a
higher model tier and keeps it only if it is measurably better. This module holds
the *pure* decision logic — which signals trigger an escalation, what the next tier
up is, and whether we can still afford one — so it is unit-testable without the
pipeline. The re-run + keep-if-better orchestration lives in query_pipeline.

The two halves the feature needs already exist independently: the quality signals
(verifier drop-rate on prose, citation-gate diagnostics on synthesis) and per-call
observability (usage aggregate). This is just the controller.
"""

from __future__ import annotations

from typing import Optional

from utils.config import EscalationSettings, LLMSettings

from .usage import current_usage


def escalation_event(stage: str, reason: str, from_model: str, to_model: str,
                     improved: bool, **extra) -> dict:
    """One record for the trace's ``escalations`` array. ``improved`` is whether the
    escalated result was actually kept (False = attempted but the cheap result was
    at least as good, so it was retained)."""
    return {"stage": stage, "reason": reason, "from_model": from_model,
            "to_model": to_model, "improved": improved, **extra}


def prose_score(answer) -> tuple:
    """Higher is better: more supported details, fewer dropped claims, non-empty
    summary. Used to decide whether an escalated answer beats the cheap one."""
    return (len(answer.details), -len(answer.unsupported),
            1 if (answer.summary or "").strip() else 0)


class EscalationPolicy:
    def __init__(self, settings: EscalationSettings, llm: LLMSettings):
        self.s = settings
        self.llm = llm

    @property
    def enabled(self) -> bool:
        return self.s.enabled

    # ── model ladder ─────────────────────────────────────────────────────────
    def _tiers(self) -> list[str]:
        """answer < complex < escalation, de-duplicated (drops None + repeats)."""
        out: list[str] = []
        for m in (self.llm.answer_model, self.llm.complex_model, self.llm.escalation_model):
            if m and m not in out:
                out.append(m)
        return out

    def next_model(self, current: str) -> str | None:
        """The tier immediately above ``current`` (None if already at the top, or if
        no higher tier is configured). An off-ladder model (e.g. a per-call override)
        escalates straight to the top tier when that is different."""
        tiers = self._tiers()
        if current in tiers:
            i = tiers.index(current)
            return tiers[i + 1] if i + 1 < len(tiers) else None
        return tiers[-1] if tiers and tiers[-1] != current else None

    # ── budget / caps ────────────────────────────────────────────────────────
    def can_afford(self, n_done: int) -> bool:
        """True if we may still escalate: under the per-request cap AND this
        request's LLM spend so far is under the budget."""
        if n_done >= self.s.max_per_request:
            return False
        agg = current_usage()
        spent = agg.cost_usd if agg is not None else 0.0
        return spent < self.s.cost_budget_usd

    # ── signals ──────────────────────────────────────────────────────────────
    def prose_signal(self, answer) -> str | None:
        """Reason to escalate a prose answer, or None. A floor-refusal is skipped —
        it means there was no evidence, so a stronger model can't help."""
        if answer.refused:
            return None
        n_det = len(answer.details)
        n_uns = len(answer.unsupported)
        total = n_det + n_uns
        if n_det == 0:
            return "empty_answer" if n_uns == 0 else "all_unsupported"
        if total and (n_uns / total) >= self.s.verifier_drop_ratio:
            return "verifier_drop"
        return None

    def synth_signal(self, diag) -> str | None:
        """Reason to escalate a shock-vector synthesis, or None. Skipped when there
        was no upstream evidence to synthesize from."""
        if getattr(diag, "skipped", None):
            return None
        emitted = len(diag.emitted_fields)
        kept = len(diag.kept_fields)
        if emitted == 0:
            return "llm_empty"
        if kept == 0:
            return "all_dropped"
        if (emitted - kept) / emitted >= self.s.gate_loss_ratio:
            return "gate_loss"
        return None
