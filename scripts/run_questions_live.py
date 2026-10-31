"""Run eval questions end-to-end against the REAL vector DB + OpenAI.

Reads questions from a golden YAML (the `question:` field of each item) — e.g.
configs/eval/golden_user_facing.yaml or configs/eval/golden_stress_backend.yaml —
or a single --question, builds the production pipeline ONCE (so the heavy bge-m3
embedder + cross-encoder reranker load a single time), and runs each question in
one of two modes:

  answer      -> retrieval + cited RAG prose answer   (pipeline.answer)
  synthesize  -> retrieval + gated shock vector        (pipeline.synthesize)

The pipeline honours configs/rag.yaml, which points qdrant at http://localhost:6333
and the LLM at OpenAI — so this hits live infra (real Qdrant, real OpenAI calls).

Prereqs: Qdrant server up on :6333 with the `macro_chunks` collection; OPENAI_API_KEY
in .env. Run with `src` on PYTHONPATH, in the py312 env:

  $env:PYTHONPATH="src"
  conda run -n py312 python scripts/run_questions_live.py \
      --file configs/eval/golden_user_facing.yaml --mode answer --limit 2
  conda run -n py312 python scripts/run_questions_live.py \
      --file configs/eval/golden_stress_backend.yaml --mode synthesize --limit 2 \
      --horizon-days 180
  conda run -n py312 python scripts/run_questions_live.py \
      --question "Outlook for the term premium?" --mode answer
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from collections import Counter
from pathlib import Path

import yaml


def _load_questions(file: str | None, question: str | None,
                    limit: int | None) -> list[str]:
    if question:
        return [question]
    data = yaml.safe_load(Path(file).read_text(encoding="utf-8")) or {}
    qs = [item["question"] for item in data.get("questions", []) if "question" in item]
    return qs[:limit] if limit else qs


def _src_counts(chunks) -> dict:
    return dict(Counter(c.payload.get("source_id") for c in chunks).most_common())


def _hr(title: str) -> None:
    print("\n" + "=" * 80 + f"\n{title}\n" + "=" * 80)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="golden YAML to read questions from")
    src.add_argument("--question", help="a single ad-hoc question")
    p.add_argument("--mode", choices=["answer", "synthesize"], default="answer")
    p.add_argument("--limit", type=int, default=None, help="cap questions from --file")
    p.add_argument("--horizon-days", type=int, default=None, help="synthesize horizon")
    args = p.parse_args(argv)

    # src/ must be importable — mirror the CLAUDE.md PYTHONPATH=src convention even
    # when invoked as a bare script path.
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from pipelines.query_pipeline import build_pipeline
    from utils.logging import setup_logging

    setup_logging()
    questions = _load_questions(args.file, args.question, args.limit)
    print(f"running {len(questions)} question(s) in mode={args.mode} against live "
          f"Qdrant + OpenAI …")
    pipe = build_pipeline()
    pipe.warm()

    for i, q in enumerate(questions, 1):
        _hr(f"[{i}/{len(questions)}] {textwrap.shorten(q, 100)}")
        if args.mode == "answer":
            r = pipe.answer(q)
            print("route     :", r.analysis.route, "| refused:", r.answer.refused,
                  "| cached:", r.cached)
            print("chunks    :", len(r.chunks), "| by source_id:", _src_counts(r.chunks))
            print("summary   :\n" + textwrap.indent(
                textwrap.fill(r.answer.summary, 92), "  "))
            for d in r.answer.details[:5]:
                print("   -", textwrap.shorten(d.text, 150), d.chunk_ids[:2])
        else:
            r = pipe.synthesize(q, horizon_days=args.horizon_days)
            qr = r.query
            print("route     :", qr.analysis.route, "| refused:", qr.answer.refused)
            print("chunks    :", len(qr.chunks), "| by source_id:", _src_counts(qr.chunks))
            print("world_view:", r.world_view_id)
            if r.diagnostics is not None:
                print("diagnostics:", r.diagnostics.summary())
            print(f"shock_vector ({len(r.shock_vector)} gated field(s)):")
            for name, f in r.shock_vector.items():
                print(f"   {name:11s} {f.value:+.1f} {f.unit:4s} [{f.grounding_mode}] "
                      f"cites={f.chunk_ids[:3]}")
                if f.rationale:
                    print("      ", textwrap.shorten(f.rationale, 120))
            print("answer    :\n" + textwrap.indent(
                textwrap.fill(qr.answer.summary, 92), "  "))
    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
