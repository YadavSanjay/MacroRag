"""Tests for tools/standards/check_standards.py against real temporary git repos and real ruff."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "standards" / "check_standards.py"
_spec = importlib.util.spec_from_file_location("check_standards", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cs = importlib.util.module_from_spec(_spec)
sys.modules["check_standards"] = cs
_spec.loader.exec_module(cs)

PYPROJECT = """\
[tool.ruff]
target-version = "py312"
extend = "ruff-burndown.toml"

[tool.ruff.lint]
select = ["E", "F"]

[tool.standards]
base = "main"
protected = ["locked/*"]
"""


def git(root: Path, *args: str) -> str:
    # Fixed argument list for the git on PATH, run in a temporary repo; no shell (S603/S607 are
    # exempted for this directory in the repo config).
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def write(root: Path, path: str, text: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one commit on `main` and a checked-out `feature` branch."""
    git(tmp_path, "init", "-q", "-b", "main")
    write(tmp_path, "pyproject.toml", PYPROJECT)
    write(tmp_path, "ruff-burndown.toml", "[lint.per-file-ignores]\n")
    write(tmp_path, "requirements-dev.txt", "ruff==0.14.2\npytest==9.0.1\n")
    write(tmp_path, "pkg/old.py", "def old(x):\n    return x\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def cfg_base(root: Path) -> tuple[object, str]:
    cfg = cs.load_config(root)
    base = cs.merge_base(cfg)
    assert base is not None
    return cfg, base


def test_load_config_rejects_unknown_key(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", "[tool.standards]\nmypy_at_stp = true\n")
    with pytest.raises(ValueError, match="mypy_at_stp"):
        cs.load_config(tmp_path)


def test_load_config_reads_standards_toml_without_pyproject(tmp_path: Path) -> None:
    text = '[tool.standards]\nmypy_at_stop = true\nprotected = ["a/*"]\n'
    write(tmp_path, "standards.toml", text)
    cfg = cs.load_config(tmp_path)
    assert cfg.mypy_at_stop is True
    assert cfg.protected == ("a/*",)


def test_changed_files_include_committed_and_untracked_but_not_protected(repo: Path) -> None:
    write(repo, "pkg/committed.py", "A = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    write(repo, "pkg/untracked.py", "B = 2\n")
    write(repo, "locked/spec_test.py", "C = 3\n")
    cfg, base = cfg_base(repo)
    assert cs.changed_files(cfg, base) == ["pkg/committed.py", "pkg/untracked.py"]


def test_changed_lines_only_reports_added_lines(repo: Path) -> None:
    write(repo, "pkg/old.py", "def old(x):\n    return x\n\n\ndef added():\n    return 1\n")
    cfg, base = cfg_base(repo)
    assert cs.changed_lines(cfg, base, "pkg/old.py") == {3, 4, 5, 6}
    write(repo, "pkg/new.py", "X = 1\n")
    assert cs.changed_lines(cfg, base, "pkg/new.py") is None


def test_new_code_flags_only_new_public_function_without_docstring(repo: Path) -> None:
    old = '"""Module."""\n\n\ndef old(x):\n    return x\n'
    write(repo, "pkg/old.py", old + "\n\ndef added(y):\n    return y\n")
    cfg, base = cfg_base(repo)
    findings = cs.new_code(cfg, base, ["pkg/old.py"])
    assert any("pkg/old.py:8: D103" in f for f in findings)
    assert not any(":4: D103" in f for f in findings)


def test_ratchet_flags_new_burndown_entry_and_accepts_trailer(repo: Path) -> None:
    write(repo, "ruff-burndown.toml", '[lint.per-file-ignores]\n"pkg/old.py" = ["E501"]\n')
    cfg, base = cfg_base(repo)
    assert cs.ratchet(cfg, base) == ["burn-down entry added: pkg/old.py E501"]
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "widen\n\nLint-Ratchet: initial burn-down")
    assert cs.ratchet_trailers(cfg, base) == ["initial burn-down"]
    assert cs.main(["--root", str(repo), "ratchet"]) == 0


def test_ratchet_allows_shrinking_burndown(repo: Path) -> None:
    write(repo, "ruff-burndown.toml", '[lint.per-file-ignores]\n"pkg/old.py" = ["E501"]\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    git(repo, "branch", "-f", "main")
    write(repo, "ruff-burndown.toml", "[lint.per-file-ignores]\n")
    cfg, base = cfg_base(repo)
    assert cs.ratchet(cfg, base) == []


def test_ratchet_flags_removed_rule_new_ignore_and_exclude(repo: Path) -> None:
    loosened = PYPROJECT.replace('select = ["E", "F"]', 'select = ["F"]\nignore = ["F401"]')
    loosened = loosened.replace(
        'extend = "ruff-burndown.toml"', 'extend = "ruff-burndown.toml"\nextend-exclude = ["pkg"]'
    )
    write(repo, "pyproject.toml", loosened)
    cfg, base = cfg_base(repo)
    assert cs.ratchet(cfg, base) == [
        "ruff rule no longer selected: E",
        "ruff rule newly ignored: F401",
        "ruff path newly excluded: pkg",
    ]


def test_ratchet_flags_mypy_override_and_loosened_flag(repo: Path) -> None:
    write(repo, "pyproject.toml", PYPROJECT + "\n[tool.mypy]\nstrict = true\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "mypy")
    git(repo, "branch", "-f", "main")
    override = '\n[[tool.mypy.overrides]]\nmodule = "pkg.*"\nignore_errors = true\n'
    write(repo, "pyproject.toml", PYPROJECT + "\n[tool.mypy]\nstrict = false\n" + override)
    cfg, base = cfg_base(repo)
    assert cs.ratchet(cfg, base) == [
        "mypy override added: [mypy-pkg.*]",
        "mypy setting loosened: [mypy] strict = false",
    ]


def test_ratchet_flags_unpinned_and_changed_pins(repo: Path) -> None:
    write(repo, "requirements-dev.txt", "ruff==0.15.0\npytest>=9\nnewtool\nhttpx==0.28.1\n")
    cfg, base = cfg_base(repo)
    assert cs.ratchet(cfg, base) == [
        "requirement not pinned with ==: newtool",
        "requirement not pinned with ==: pytest",
        "pinned version changed: ruff 0.14.2 -> 0.15.0",
    ]


def test_burndown_initial_then_refuses_growth_and_allows_shrink(repo: Path) -> None:
    write(repo, "pkg/bad.py", "import os\nimport sys\n")
    cfg = cs.load_config(repo)
    cs.burndown(cfg, initial=True)
    assert '"pkg/bad.py" = ["F401"]' in (repo / "ruff-burndown.toml").read_text(encoding="utf-8")
    write(repo, "pkg/worse.py", "import json\n")
    assert cs.burndown(cfg, initial=False) == ["pkg/worse.py F401"]
    assert "worse" not in (repo / "ruff-burndown.toml").read_text(encoding="utf-8")
    (repo / "pkg" / "worse.py").unlink()
    write(repo, "pkg/bad.py", "X = 1\n")
    assert cs.burndown(cfg, initial=False) == []
    assert '"pkg/bad.py"' not in (repo / "ruff-burndown.toml").read_text(encoding="utf-8")


def test_burndown_absorbs_noqa_made_unused_by_a_file_entry(repo: Path) -> None:
    write(repo, "pyproject.toml", PYPROJECT.replace('["E", "F"]', '["E", "F", "RUF100"]'))
    write(repo, "pkg/mixed.py", "import os  # noqa: F401\nimport sys\n")
    cfg = cs.load_config(repo)
    cs.burndown(cfg, initial=True)
    text = (repo / "ruff-burndown.toml").read_text(encoding="utf-8")
    assert '"pkg/mixed.py" = ["F401", "RUF100"]' in text
    assert cs.ruff_findings(cfg, []) == []


def test_stop_reports_once_then_skips_until_files_change(repo: Path) -> None:
    cfg = cs.load_config(repo)
    assert cs.stop(cfg) == ("", False)
    write(repo, "pkg/new.py", "import os\n")
    report, found = cs.stop(cfg)
    assert found
    assert "pkg/new.py:1: F401" in report
    assert cs.stop(cfg) == ("", False)
    write(repo, "pkg/new.py", '"""Module."""\n\nX = 1\n')
    assert cs.stop(cfg) == ("", False)


def run_hook(
    repo: Path, event: dict[str, object], monkeypatch: pytest.MonkeyPatch, prefix: bytes = b""
) -> dict[str, object]:
    raw = io.BytesIO(prefix + json.dumps(event).encode("utf-8"))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(raw, encoding="utf-8"))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    cs._hook_stop(cs.load_config(repo))
    return json.loads(out.getvalue()) if out.getvalue() else {}


def test_hook_is_advisory_by_default(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(repo, "pkg/new.py", "import os\n")
    response = run_hook(repo, {}, monkeypatch)
    assert "F401" in str(response["systemMessage"])
    assert "decision" not in response


def test_hook_blocks_once_when_hard(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(repo, "pyproject.toml", PYPROJECT + "advisory = false\n")
    write(repo, "pkg/new.py", "import os\n")
    assert run_hook(repo, {}, monkeypatch)["decision"] == "block"
    write(repo, "pkg/new.py", "import os\nimport sys\n")
    assert "systemMessage" in run_hook(repo, {"stop_hook_active": True}, monkeypatch)


def test_hook_reads_event_with_utf8_bom(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # PowerShell 5.1 prefixes piped input with a BOM; stop_hook_active must still be honoured.
    write(repo, "pyproject.toml", PYPROJECT + "advisory = false\n")
    write(repo, "pkg/new.py", "import os\n")
    response = run_hook(repo, {"stop_hook_active": True}, monkeypatch, prefix=b"\xef\xbb\xbf")
    assert "systemMessage" in response
    assert "decision" not in response
