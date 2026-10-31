"""Block-and-allow tests for the dev-harness hooks in `.claude/hooks/` (§L).

Every guard is tested both ways: cases it must block and cases it must allow. The
protected-path guard runs as a real subprocess, since its exit code and stdout are the
contract. The PowerShell hooks (ruff fixer, Stop gate) run under Windows PowerShell with
a fake interpreter standing in for py312, so no ruff or pytest run happens here; they
are skipped where `powershell` is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / ".claude" / "hooks"
GUARD = HOOKS / "guard_protected_paths.py"
STOP_GATE = HOOKS / "test-gate.ps1"
FIXER = HOOKS / "format-python.ps1"

POWERSHELL = shutil.which("powershell")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell absent")


def _run_guard(payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD)], input=payload, capture_output=True, text=True,
        timeout=30, check=False,
    )


def _event(tool: str, **tool_input: Any) -> str:
    return json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    )


def _denied(proc: subprocess.CompletedProcess[str]) -> bool:
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def _allowed(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode == 0 and proc.stdout.strip() == ""


# --------------------------------------------------------------------------- guard: file tools

PROTECTED_PATHS = [
    ".env",
    "C:/work/MacroRAG/.env",
    r"C:\work\MacroRAG\.env",
    "infra/.env.local",
    ".env.production",
    ".ENV",
    "id_rsa",
    "keys/server.key",
    "certs/tls.pem",
    "deploy/service-account-prod.json",
    "secrets.json",
    ".git-credentials",
    ".pgpass",
]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
@pytest.mark.parametrize("tool", ["Read", "Edit", "Write", "MultiEdit"])
def test_guard_denies_protected_files(tool: str, path: str) -> None:
    assert _denied(_run_guard(_event(tool, file_path=path)))


def test_guard_denies_notebook_edit_of_protected_file() -> None:
    assert _denied(_run_guard(_event("NotebookEdit", notebook_path="x/.env")))


def test_guard_denies_symlink_to_protected_file(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("KEY=1", encoding="utf-8")
    link = tmp_path / "harmless.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted on this machine")
    assert _denied(_run_guard(_event("Read", file_path=str(link))))


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        ".env.template",
        "src/utils/config.py",
        "configs/rag.yaml",
        "docs/harness/REAUDIT.md",
        "environment.yml",
        "keyboard.py",
        "src/ingest/common/dateextract.py",
    ],
)
@pytest.mark.parametrize("tool", ["Read", "Edit", "Write"])
def test_guard_allows_ordinary_files(tool: str, path: str) -> None:
    assert _allowed(_run_guard(_event(tool, file_path=path)))


# --------------------------------------------------------------------------- guard: shell tools


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "Get-Content .env",
        "type C:\\repo\\.env",
        "grep KEY infra/.env.local",
        "cp .env /tmp/x",
        "echo $(< .ENV)",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_denies_shell_commands_naming_env(tool: str, command: str) -> None:
    assert _denied(_run_guard(_event(tool, command=command)))


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "cat .env.example",
        "conda run -n py312 python -m pytest test/ -q",
        "grep -rn environment src/",
        "ls .envrc-notes",
        "echo MACRORAG_DOTENV=0",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_allows_ordinary_shell_commands(tool: str, command: str) -> None:
    assert _allowed(_run_guard(_event(tool, command=command)))


# --------------------------------------------------------------------------- guard: fail closed


@pytest.mark.parametrize("payload", ["", "not json", "[1, 2]", "null"])
def test_guard_blocks_unparseable_events(payload: str) -> None:
    proc = _run_guard(payload)
    assert proc.returncode == 2
    assert "Blocked" in proc.stderr


@pytest.mark.parametrize("tool", ["Read", "Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_guard_blocks_file_tool_without_path(tool: str) -> None:
    proc = _run_guard(_event(tool))
    assert proc.returncode == 2
    assert "names no target path" in proc.stderr


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_blocks_shell_tool_without_command(tool: str) -> None:
    assert _run_guard(_event(tool, command="  ")).returncode == 2


def test_guard_ignores_other_tools_without_a_path() -> None:
    assert _allowed(_run_guard(_event("WebSearch", query="fed funds")))


# --------------------------------------------------------------------------- PowerShell hooks


def _fake_python(tmp_path: Path) -> Path:
    """A stand-in interpreter: logs its arguments and exits with %FAKE_RC%."""
    fake = tmp_path / "fakepy.cmd"
    fake.write_text('@echo %* >> "%FAKE_LOG%"\r\n@exit /b %FAKE_RC%\r\n', encoding="ascii")
    return fake


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _repo_with_change(tmp_path: Path, rel: str) -> Path:
    """A git repo whose working tree changes `rel` relative to HEAD."""
    repo = tmp_path / "repo"
    (repo / Path(rel).parent).mkdir(parents=True)
    (repo / rel).write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / rel).write_text("x = 2\n", encoding="utf-8")
    return repo


def _run_ps(
    script: Path, cwd: Path, payload: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        input=payload, cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True,
        timeout=120, check=False,
    )


def _stop_env(tmp_path: Path, rc: int) -> dict[str, str]:
    return {
        "MACRORAG_HOOK_PYTHON": str(_fake_python(tmp_path)),
        "FAKE_RC": str(rc),
        "FAKE_LOG": str(tmp_path / "calls.log"),
    }


def _pytest_ran(tmp_path: Path) -> bool:
    log = tmp_path / "calls.log"
    return log.exists() and "-m pytest" in log.read_text(encoding="ascii", errors="replace")


@needs_powershell
def test_stop_gate_blocks_on_red_tests(tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path, "src/mod.py")
    proc = _run_ps(STOP_GATE, repo, "{}", _stop_env(tmp_path, 1))
    assert proc.returncode == 2
    assert "RED" in proc.stderr
    assert _pytest_ran(tmp_path)


@needs_powershell
def test_stop_gate_allows_on_green_and_records_it(tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path, "src/mod.py")
    env = _stop_env(tmp_path, 0)
    assert _run_ps(STOP_GATE, repo, "{}", env).returncode == 0
    assert (repo / ".claude" / "state" / "test-gate.json").exists()
    # The same fingerprint is already green: a second stop skips pytest.
    (tmp_path / "calls.log").unlink()
    assert _run_ps(STOP_GATE, repo, "{}", env).returncode == 0
    assert not _pytest_ran(tmp_path)


@needs_powershell
def test_stop_gate_skips_tests_for_docs_only_change(tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path, "docs/notes.md")
    assert _run_ps(STOP_GATE, repo, "{}", _stop_env(tmp_path, 1)).returncode == 0
    assert not _pytest_ran(tmp_path)


@needs_powershell
def test_stop_gate_does_not_loop(tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path, "src/mod.py")
    proc = _run_ps(STOP_GATE, repo, '{"stop_hook_active": true}', _stop_env(tmp_path, 1))
    assert proc.returncode == 0
    assert not _pytest_ran(tmp_path)


@needs_powershell
def test_stop_gate_fails_open_without_interpreter(tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path, "src/mod.py")
    env = {"MACRORAG_HOOK_PYTHON": str(tmp_path / "missing.exe")}
    assert _run_ps(STOP_GATE, repo, "{}", env).returncode == 0


@needs_powershell
@pytest.mark.parametrize("payload", ["not json", _event("Edit", file_path="notes.md"), "{}"])
def test_fixer_never_blocks(tmp_path: Path, payload: str) -> None:
    assert _run_ps(FIXER, tmp_path, payload, {}).returncode == 0
