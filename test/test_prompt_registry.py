"""Prompt registry: base resolution + system fidelity, render, provenance
stamping, and the pin > provider+series > base fallback chain."""

from __future__ import annotations

import pytest

from llm.registry import PromptRegistry, get_registry
from llm.usage import prompt_context, record_call, track_usage, usage_snapshot

# The 8 migrated tasks and their promoted system-role strings (must stay
# byte-identical to the pre-registry inline constants).
EXPECT_SYS = {
    "plan": "You output only strict JSON. No prose, no code fences.",
    "plan_execute": "You output only strict JSON. No prose, no code fences.",
    "next_hop": "You output only strict JSON. No prose, no code fences.",
    "answer": "You are a precise macro analyst. You output only strict JSON.",
    "verify": "You are a strict fact-checker. You output only strict JSON.",
    "judge": "You are a strict evaluation judge. You output only strict JSON.",
    "synthesize": "You are a precise macro stress synthesizer. You output only strict JSON.",
    "discover_factors": ("You are a precise macro risk analyst. You map a portfolio to the "
                         "macro risk factors that drive it. You output only strict JSON."),
}


@pytest.mark.parametrize("task,system", EXPECT_SYS.items())
def test_base_resolution_system_is_byte_identical(task, system):
    rp = get_registry().resolve(task)
    assert rp.system == system
    assert rp.variant_id == "base"
    assert len(rp.content_hash) == 16


def test_render_substitutes_and_is_brace_safe():
    rp = get_registry().resolve("answer")
    out = rp.render(question="Q", context="C", data="D")
    assert "{question}" not in out and "Q" in out


def test_content_hash_deterministic_and_task_distinct():
    reg = get_registry()
    assert reg.resolve("answer").content_hash == reg.resolve("answer").content_hash
    hashes = {reg.resolve(t).content_hash for t in EXPECT_SYS}
    # tasks sharing the strict-json system still differ by their user template
    assert len(hashes) >= 6


def test_prompt_context_stamps_provenance_into_trace():
    with track_usage("t"):
        rp = get_registry().resolve("synthesize")
        with prompt_context(rp.variant_id, rp.content_hash):
            record_call(provider="fake", model="m", prompt_tokens=1, completion_tokens=1,
                        purpose="synthesize", system=rp.system, user="u", response="r")
        snap = usage_snapshot()
    call = snap["call_log"][0]
    assert call["prompt_variant"] == "base"
    assert call["prompt_hash"] == rp.content_hash


def test_no_prompt_context_leaves_provenance_none():
    with track_usage("t"):
        record_call(provider="fake", model="m", purpose="x")
        snap = usage_snapshot()
    call = snap["call_log"][0]
    assert call["prompt_variant"] is None and call["prompt_hash"] is None


def _synthetic_registry(tmp_path) -> PromptRegistry:
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "strict-json.md").write_text("SYS")
    tdir = tmp_path / "tasks" / "demo"
    (tdir / "variants").mkdir(parents=True)
    (tdir / "base.md").write_text("BASE {q}")
    (tdir / "base.meta.yaml").write_text("task: demo\nvariant_id: base\nsystem: strict-json\n")
    (tdir / "variants" / "anthropic.claude-opus-4.v2.md").write_text("VARIANT {q}")
    (tdir / "variants" / "anthropic.claude-opus-4.v2.meta.yaml").write_text(
        "task: demo\nvariant_id: anthropic.claude-opus-4.v2\nprovider: anthropic\n"
        "model_series: claude-opus-4\nversion: 2\nstatus: active\nsystem: strict-json\n")
    return PromptRegistry(tmp_path)


def test_variant_fallback_chain(tmp_path):
    reg = _synthetic_registry(tmp_path)
    # provider + model-series match -> the pinned variant
    assert reg.resolve("demo", provider="anthropic",
                       model="claude-opus-4-8").variant_id == "anthropic.claude-opus-4.v2"
    # non-matching provider -> base
    assert reg.resolve("demo", provider="openai", model="gpt-5.4").variant_id == "base"
    # no provider context -> base
    assert reg.resolve("demo").variant_id == "base"
    # explicit pin wins
    assert reg.resolve("demo", pin="anthropic.claude-opus-4.v2").variant_id \
        == "anthropic.claude-opus-4.v2"
    # variant body is used when selected
    assert reg.resolve("demo", provider="anthropic",
                       model="claude-opus-4-8").render(q="X") == "VARIANT X"


# ── manifest prompt-drift signal ─────────────────────────────────────────────
# Regression: prompt_hash() used to glob a non-existent ``prompt/`` dir for
# ``*.txt``, so it always returned the sha256 of EMPTY input — the manifest's
# prompt provenance was a constant and prompt drift was invisible. It must hash
# the real ``prompts/`` registry (nested .md/.yaml/.json).

def test_prompt_hash_covers_the_real_registry():
    import hashlib

    from utils.manifest import _PROMPT_DIR, _PROMPT_SUFFIXES, prompt_hash

    assert _PROMPT_DIR.name == "prompts" and _PROMPT_DIR.is_dir()
    files = [p for p in _PROMPT_DIR.rglob("*")
             if p.is_file() and p.suffix.lower() in _PROMPT_SUFFIXES]
    assert files, "prompt registry should not be empty"
    assert prompt_hash() != hashlib.sha256().hexdigest()[:16]   # not the empty digest


def test_prompt_hash_is_stable_and_content_sensitive(prompts_copy):
    from utils.manifest import prompt_hash

    before = prompt_hash()
    assert prompt_hash() == before                              # deterministic

    target = next(iter(sorted((prompts_copy / "system").glob("*.md"))))
    original = target.read_bytes()
    target.write_bytes(original + b"\n<!-- drift -->\n")
    assert prompt_hash() != before                              # detects a change
    target.write_bytes(original)
    assert prompt_hash() == before                              # and restores exactly
