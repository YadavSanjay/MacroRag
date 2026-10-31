"""Phase 3: quality-signal-driven model escalation.

Pure policy logic (ladder, budget/caps, prose + synth signals) plus the pipeline
orchestration for the prose path (retry on a higher tier, keep-if-better, record).
"""

from __future__ import annotations

from llm.escalation import EscalationPolicy, escalation_event, prose_score
from llm.fake import FakeLLM
from llm.usage import record_call, track_usage
from pipelines.query_pipeline import QueryPipeline
from synthesis.diagnostics import SynthesisDiagnostics
from utils.config import EscalationSettings, LLMSettings, ModelProfile, RagSettings
from utils.schema import Answer, AnswerDetail, RetrievedChunk


def _policy(llm: LLMSettings, **esc) -> EscalationPolicy:
    return EscalationPolicy(EscalationSettings(enabled=True, **esc), llm)


# ── model ladder ─────────────────────────────────────────────────────────────

def test_next_model_default_two_tier_ladder():
    p = _policy(LLMSettings())          # openai: answer=nano, complex=gpt-5.4, no escalation
    assert p.next_model("gpt-5.4-nano") == "gpt-5.4"
    assert p.next_model("gpt-5.4") is None          # already top tier


def test_next_model_with_escalation_tier():
    llm = LLMSettings(provider="openai", profiles={
        "openai": ModelProfile(plan="p", answer="a", complex="c", escalation="e")})
    p = _policy(llm)
    assert llm.escalation_model == "e"
    assert p.next_model("a") == "c"
    assert p.next_model("c") == "e"
    assert p.next_model("e") is None


def test_next_model_off_ladder_goes_to_top():
    p = _policy(LLMSettings())
    assert p.next_model("some-override") == "gpt-5.4"   # jump to top when different


# ── budget / caps ────────────────────────────────────────────────────────────

def test_can_afford_respects_per_request_cap():
    p = _policy(LLMSettings(), max_per_request=1)
    assert p.can_afford(0) is True
    assert p.can_afford(1) is False


def test_can_afford_respects_cost_budget():
    p = _policy(LLMSettings(), cost_budget_usd=0.01)
    with track_usage("q") as agg:
        assert p.can_afford(0) is True     # nothing spent yet
        agg.cost_usd = 0.02                # over budget
        assert p.can_afford(0) is False


# ── prose signal ─────────────────────────────────────────────────────────────

def test_prose_signal_refusal_is_not_escalated():
    p = _policy(LLMSettings())
    assert p.prose_signal(Answer(summary="", refused=True)) is None


def test_prose_signal_empty_and_all_unsupported():
    p = _policy(LLMSettings())
    assert p.prose_signal(Answer(summary="x")) == "empty_answer"          # 0 details
    assert p.prose_signal(Answer(summary="x", unsupported=["a", "b"])) == "all_unsupported"


def test_prose_signal_drop_ratio_threshold():
    p = _policy(LLMSettings(), verifier_drop_ratio=0.34)
    d = [AnswerDetail("kept")]
    assert p.prose_signal(Answer(summary="x", details=d, unsupported=["u"])) == "verifier_drop"  # 1/2
    assert p.prose_signal(Answer(summary="x", details=[AnswerDetail("a"), AnswerDetail("b")],
                                 unsupported=["u"])) is None                                       # 1/3 < .34


# ── synth signal ─────────────────────────────────────────────────────────────

def test_synth_signal_cases():
    p = _policy(LLMSettings(), gate_loss_ratio=0.34)
    assert p.synth_signal(SynthesisDiagnostics(skipped="no_chunks")) is None
    assert p.synth_signal(SynthesisDiagnostics(emitted_fields=[])) == "llm_empty"
    d = SynthesisDiagnostics(emitted_fields=["a", "b"], kept_fields=[])
    assert p.synth_signal(d) == "all_dropped"
    d = SynthesisDiagnostics(emitted_fields=["a", "b", "c"], kept_fields=["a"])   # loss 2/3
    assert p.synth_signal(d) == "gate_loss"
    d = SynthesisDiagnostics(emitted_fields=["a", "b", "c"], kept_fields=["a", "b"])  # 1/3 < .34
    assert p.synth_signal(d) is None


def test_prose_score_ordering():
    good = Answer(summary="s", details=[AnswerDetail("d")])
    bad = Answer(summary="")
    assert prose_score(good) > prose_score(bad)


def test_escalation_event_shape():
    ev = escalation_event("generate", "verifier_drop", "cheap", "strong", True)
    assert ev == {"stage": "generate", "reason": "verifier_drop",
                  "from_model": "cheap", "to_model": "strong", "improved": True}


# ── pipeline orchestration (prose) ───────────────────────────────────────────

class _ModelFake(FakeLLM):
    """Returns a canned response chosen by the resolved model id."""
    def __init__(self, by_model: dict[str, str]):
        super().__init__()
        self.by_model = by_model

    def complete(self, *, system, user, temperature=None, model=None, purpose=None):
        text = self.by_model.get(model, "{}")
        self.calls.append((system, user))
        self.models_used.append(model)
        record_call(provider="fake", model=model, purpose=purpose,
                    system=system, user=user, response=text)
        return text


def _pipe(by_model: dict[str, str], *, enabled=True) -> QueryPipeline:
    s = RagSettings(
        llm={"provider": "openai", "profiles": {
            "openai": {"plan": "cheap", "answer": "cheap", "complex": "strong"}}},
        escalation={"enabled": enabled},
        retrieval={"verify_faithfulness": False})
    pipe = QueryPipeline.__new__(QueryPipeline)   # bypass heavy __init__
    pipe.llm = _ModelFake(by_model)
    pipe.s = s
    pipe.escalation = EscalationPolicy(s.escalation, s.llm)
    return pipe


_CHUNKS = [RetrievedChunk("c1", "evidence text", 1.0, {"publication": "P"})]
_GOOD = '{"summary":"good","details":[{"text":"d","chunk_ids":["c1"]}],"unanswered":[]}'


def test_prose_escalation_upgrades_and_records():
    pipe = _pipe({"cheap": "{}", "strong": _GOOD})
    bad = Answer(summary="")                       # empty_answer signal
    answer, esc = pipe._maybe_escalate_prose("q", _CHUNKS, [], bad, "cheap", {})
    assert answer.summary == "good" and len(answer.details) == 1
    assert "strong" in pipe.llm.models_used         # re-generated on the higher tier
    assert len(esc) == 1
    assert esc[0]["reason"] == "empty_answer" and esc[0]["improved"] is True
    assert esc[0]["from_model"] == "cheap" and esc[0]["to_model"] == "strong"


def test_prose_escalation_keeps_original_when_not_better():
    pipe = _pipe({"cheap": "{}", "strong": "{}"})   # higher tier no better
    orig = Answer(summary="orig")
    answer, esc = pipe._maybe_escalate_prose("q", _CHUNKS, [], orig, "cheap", {})
    assert answer.summary == "orig"                 # original retained
    assert esc[0]["improved"] is False              # attempt still recorded


def test_prose_escalation_disabled_is_noop():
    pipe = _pipe({"cheap": "{}", "strong": _GOOD}, enabled=False)
    bad = Answer(summary="")
    answer, esc = pipe._maybe_escalate_prose("q", _CHUNKS, [], bad, "cheap", {})
    assert answer is bad and esc == []
    assert pipe.llm.models_used == []               # no LLM call at all
