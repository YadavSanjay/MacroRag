"""Macro RAG CLI: index the corpus, query it, or run retrieval eval.

Examples
--------
    python -m pipelines.cli index
    python -m pipelines.cli query "severely adverse unemployment path in DFAST 2026"
    python -m pipelines.cli query "FOMC dot plot" --no-llm
    python -m pipelines.cli eval
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import utils.config
from utils.config import load_settings
from utils.logging import setup_logging


def matrix_models(models_arg: str | None, judge_model_arg: str | None,
                  llm: utils.config.LLMSettings) -> tuple[list[str], str]:
    """The eval-matrix model list and judge model, all priced (spec llm-model-registry C4).

    Args:
        models_arg: The ``--models`` value (comma-separated), or None.
        judge_model_arg: The ``--judge-model`` value, or None.
        llm: The loaded LLM settings, for the defaults.

    Returns:
        ``(models, judge_model)``.

    Raises:
        ValueError: Naming every unpriced id among them.
    """
    from llm.pricing import require_priced

    models = ([m.strip() for m in models_arg.split(",") if m.strip()] if models_arg
              else [llm.answer_model, llm.complex_model])
    judge_model = judge_model_arg or llm.plan_model
    require_priced([*models, judge_model])
    return models, judge_model


def _cmd_index(args) -> int:
    if args.incremental:
        from .index_pipeline import run_incremental
        r = run_incremental(dry_run=args.dry_run)
        tag = "DRY-RUN (nothing written)" if args.dry_run else "incremental refresh"
        print(f"\n{tag}: scanned={r['scanned']} new={r['new']} changed={r['changed']} "
              f"unchanged={r['unchanged']} stale={r.get('stale', 0)}")
        if not args.dry_run:
            print(f"  embedded/upserted={r['indexed']} deleted={r['deleted']} "
                  f"| total chunks={r.get('chunks_total')} observations={r.get('observations')}")
        return 0
    from .index_pipeline import run
    report = run(recreate=not args.append)
    print(f"\nindexed: {report['chunks']} chunks, {report['observations']} observations")
    return 0


def _cmd_query(args) -> int:
    settings = load_settings()
    if args.no_llm:
        from .query_pipeline import build_retriever
        chunks, analysis = build_retriever(settings).retrieve(args.question)
        print(f"\nroute={analysis.route}  filters={analysis.filters}\n")
        for c in chunks:
            # Candidates past the rerank cut carry no rerank_score; show the fused score.
            score = c.rerank_score if c.rerank_score is not None else c.score
            print(f"[{score:.3f}] {c.payload.get('publisher','')} | "
                  f"{c.payload.get('publication','')} | {c.chunk_id}")
            print(f"    {c.text[:160].strip()}...")
        return 0

    from .query_pipeline import build_pipeline
    result = build_pipeline(settings).answer(args.question)
    a = result.answer
    print(f"\n=== ANSWER ===\n{a.summary}\n")
    for d in a.details:
        print(f"  - {d.text}  {d.chunk_ids}")
    if a.timeseries:
        print("\n=== DATA ===")
        for h in a.timeseries:
            print(f"  {h.source}: {len(h.rows)} rows ({h.note})")
    if a.unanswered:
        print("\nunanswered:", a.unanswered)
    return 0


def _cmd_synthesize(args) -> int:
    """Run a question through the shock-vector synthesis path (real Qdrant + LLM)
    and print the gated shock vector + grounding diagnostics. This is the
    backend/scenario-synthesis surface the AITrade stress engine consumes."""
    from .query_pipeline import build_pipeline
    result = build_pipeline(load_settings()).synthesize(
        args.question, horizon_days=args.horizon_days, scenario=args.scenario)
    qr = result.query
    print(f"\nroute={qr.analysis.route}  refused={qr.answer.refused}  "
          f"chunks={len(qr.chunks)}  world_view={result.world_view_id}")
    if result.diagnostics is not None:
        print("diagnostics:", result.diagnostics.summary())
    print(f"\n=== SHOCK VECTOR ({len(result.shock_vector)} gated field(s)) ===")
    for name, f in result.shock_vector.items():
        print(f"  {name:11s} {f.value:+.1f} {f.unit:4s} [{f.grounding_mode}] "
              f"cites={f.chunk_ids[:3]}")
        if f.rationale:
            print(f"      {f.rationale[:140]}")
    print(f"\n=== ANSWER ===\n{qr.answer.summary}")
    return 0


def _cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("api.server:app", host=args.host, port=args.port, reload=False)
    return 0


def _cmd_mcp(args) -> int:
    """Serve the same query/synthesis/discovery surface over MCP (in-process
    pipeline). stdio by default for a local subprocess integration."""
    import os
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from mcp_server.server import build_mcp
    build_mcp(principal=args.principal, warmup=True).run(transport=args.transport)
    return 0


def _cmd_eval(args) -> int:
    if getattr(args, "shock", None) is not None:
        return _cmd_eval_shock(args)
    from eval.harness import evaluate_retrieval, load_golden
    from utils.config import REPO_ROOT
    golden_path = Path(args.golden) if args.golden else \
        REPO_ROOT / "configs" / "eval" / "golden.yaml"
    golden = load_golden(golden_path)
    settings = load_settings()
    # --judge needs the full pipeline (retriever + llm); plain eval only a retriever.
    if getattr(args, "judge", False):
        from .query_pipeline import build_pipeline
        pipe = build_pipeline(settings)
        retriever = pipe.retriever
    else:
        from .query_pipeline import build_retriever
        retriever = build_retriever(settings)
    report = evaluate_retrieval(retriever, golden, k=args.k)
    print("\n=== retrieval eval ===")
    for row in report["rows"]:
        print(f"  hit@{args.k}={row[f'hit@{args.k}']:.0f} mrr={row['mrr']:.2f} "
              f"| {row['question'][:60]}")
    summary = dict(report["summary"])
    if getattr(args, "judge", False):
        from eval.harness import evaluate_answers
        print("\n=== answer-quality (LLM judge) ===")
        jr = evaluate_answers(pipe, golden)
        for row in jr["rows"]:
            print(f"  faith={row['faithfulness']:.2f} rel={row['relevancy']:.2f} "
                  f"| {row['question'][:60]}")
        summary.update(jr["summary"])
    print("\nsummary:", {k: round(v, 3) for k, v in summary.items()})
    if getattr(args, "fail_on_regression", False):
        from eval.drift import detect_drift, load_baseline
        baseline = load_baseline(load_settings().eval_baseline_path)
        if not baseline:
            print("\nwarning: no baseline saved — nothing to gate against")
            return 0
        regs = detect_drift(summary, baseline, tolerance=args.tol)
        if regs:
            print(f"\nFAIL: {len(regs)} regression(s) vs baseline:")
            for r in regs:
                print(f"  {r['metric']}: {r['baseline']} -> {r['current']} ({r['delta']})")
            return 1
        print("\nOK: no regressions vs baseline")
    return 0


def _cmd_eval_shock(args) -> int:
    """Backtest synthesized shock vectors against reference scenario paths."""
    from eval.shock_eval import evaluate_shock_accuracy, load_shock_cases
    from utils.config import REPO_ROOT

    from .query_pipeline import build_pipeline

    shock_path = Path(args.shock) if args.shock else \
        REPO_ROOT / "configs" / "eval" / "shock_reference.yaml"
    cases = load_shock_cases(shock_path)
    pipe = build_pipeline(load_settings())

    def synth_fn(question, *, horizon_days=None, base_filter=None):
        return pipe.synthesize(question, horizon_days=horizon_days,
                               base_filter=base_filter).shock_vector

    report = evaluate_shock_accuracy(synth_fn, cases, store=pipe.timeseries)
    print("\n=== shock-accuracy eval ===")
    for row in report["rows"]:
        s = row["summary"]
        print(f"  cov={s['coverage']:.2f} sign={s['sign_accuracy']:.2f} "
              f"tol={s['tolerance_rate']:.2f} | {row['id']}")
    print("\nsummary:", {k: (round(v, 3) if isinstance(v, float) else v)
                         for k, v in report["summary"].items()})
    return 0


def _cmd_eval_matrix(args) -> int:
    """Prompt × model regression matrix: run each (variant, model) cell over a
    golden set, score it, and diff against a stored baseline (design doc §6)."""
    from eval.harness import load_golden
    from eval.matrix import (
        RUNNERS,
        cells_from,
        diff,
        format_diff,
        format_report,
        has_regression,
        load_baseline,
        run_matrix,
        save_baseline,
    )
    from utils.config import REPO_ROOT

    from .query_pipeline import build_pipeline

    settings = load_settings()
    # Every model the matrix will call must be priced, before any work starts.
    models, judge_model = matrix_models(args.models, args.judge_model, settings.llm)
    golden_path = Path(args.golden) if args.golden else \
        REPO_ROOT / "configs" / "eval" / "golden.yaml"
    cases = load_golden(golden_path)
    if args.limit:
        cases = cases[:args.limit]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    cells = cells_from(variants, models)

    pipe = build_pipeline(settings)
    if args.task == "answer":
        runner = RUNNERS["answer"](
            pipe, judge_model=judge_model)
    else:
        runner = RUNNERS["synthesize"](pipe, horizon_days=args.horizon_days)

    report = run_matrix(args.task, cases, cells, runner)
    print(format_report(report))

    baseline_path = Path(args.baseline) if args.baseline else \
        REPO_ROOT / "data" / "eval" / f"matrix_{args.task}.json"
    diffs = diff(report, load_baseline(baseline_path), tol=args.tol)
    print("\n=== vs baseline ===")
    print(format_diff(diffs))
    if args.save_baseline:
        save_baseline(report, baseline_path)
        print(f"\nbaseline written: {baseline_path}")
    if args.fail_on_regression and has_regression(diffs):
        print("\nFAIL: regressions detected")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="macrorag")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("index"); pi.add_argument("--append", action="store_true")
    pi.add_argument("--incremental", action="store_true",
                    help="embed/upsert only new+changed chunks, delete departed ones "
                         "(no full re-embed)")
    pi.add_argument("--dry-run", action="store_true",
                    help="with --incremental: report the plan without writing anything")
    pq = sub.add_parser("query"); pq.add_argument("question")
    pq.add_argument("--no-llm", action="store_true")
    py = sub.add_parser("synthesize")
    py.add_argument("question")
    py.add_argument("--horizon-days", type=int, default=None)
    py.add_argument("--scenario", default=None,
                    help="extra scenario narrative folded into retrieval + synthesis")
    pe = sub.add_parser("eval"); pe.add_argument("--k", type=int, default=8)
    pe.add_argument("--golden", default=None,
                    help="path to a golden YAML (default: configs/eval/golden.yaml)")
    pe.add_argument("--shock", nargs="?", const="", default=None,
                    help="shock-accuracy mode: backtest synthesized vectors against "
                         "a reference YAML (default: configs/eval/shock_reference.yaml)")
    pe.add_argument("--fail-on-regression", action="store_true",
                    help="exit 1 if retrieval metrics regressed vs the saved baseline "
                         "(the CI gate; §K)")
    pe.add_argument("--tol", type=float, default=0.05,
                    help="metric drop below baseline that counts as a regression")
    pe.add_argument("--judge", action="store_true",
                    help="also run the LLM-judge answer-quality pass (§B): mean "
                         "faithfulness/relevancy folded into the summary + gate")
    pem = sub.add_parser("eval-matrix",
                         help="prompt × model regression matrix + baseline diff")
    pem.add_argument("--task", choices=["answer", "synthesize"], required=True)
    pem.add_argument("--models", default=None,
                     help="comma list (default: answer_model,complex_model)")
    pem.add_argument("--variants", default="latest",
                     help="comma list of prompt variant_ids (default: latest)")
    pem.add_argument("--golden", default=None)
    pem.add_argument("--judge-model", default=None)
    pem.add_argument("--horizon-days", type=int, default=None)
    pem.add_argument("--limit", type=int, default=None)
    pem.add_argument("--baseline", default=None,
                     help="baseline JSON (default: data/eval/matrix_<task>.json)")
    pem.add_argument("--save-baseline", action="store_true")
    pem.add_argument("--fail-on-regression", action="store_true")
    pem.add_argument("--tol", type=float, default=0.05)
    ps = sub.add_parser("serve")
    ps.add_argument("--host", default="127.0.0.1"); ps.add_argument("--port", type=int, default=8000)
    pm = sub.add_parser("mcp")
    pm.add_argument("--transport", default="stdio",
                    choices=["stdio", "sse", "streamable-http"])
    # --tenant kept as a deprecated alias (identity, not data isolation)
    pm.add_argument("--principal", "--tenant", dest="principal", default=None)
    args = p.parse_args(argv)
    setup_logging()
    return {"index": _cmd_index, "query": _cmd_query, "eval": _cmd_eval,
            "eval-matrix": _cmd_eval_matrix, "synthesize": _cmd_synthesize,
            "serve": _cmd_serve, "mcp": _cmd_mcp}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
