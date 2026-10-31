"""Evaluation: retrieval metrics + golden-set harness."""

from __future__ import annotations

from . import metrics, shock_metrics
from .drift import detect_drift, load_baseline, save_baseline
from .feedback_reconcile import reconcile_feedback, write_candidates
from .harness import evaluate_answers, evaluate_retrieval, load_golden
from .judge import judge_answer
from .shock_eval import derive_reference_from_store, evaluate_shock_accuracy, load_shock_cases

__all__ = ["metrics", "evaluate_retrieval", "evaluate_answers", "load_golden",
           "judge_answer", "detect_drift", "save_baseline", "load_baseline",
           "shock_metrics", "evaluate_shock_accuracy", "load_shock_cases",
           "derive_reference_from_store", "reconcile_feedback", "write_candidates"]
