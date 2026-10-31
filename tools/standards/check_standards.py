"""Coding-standards checks for the project hooks and CI.

One copy of this file lives in each repo (faultline, XbrlRag, MacroRAG) and must stay identical;
repo differences come from the ``[tool.standards]`` table in ``pyproject.toml`` (or in
``standards.toml`` when the repo has no ``pyproject.toml``). Design:
ClaudeConfig ``standards/design/plan.md``.

Subcommands:
    stop      The Stop-hook sequence: skip checks, ruff on changed files, new-code, ratchet, mypy.
    ratchet   The burn-down list only shrinks; lint/type config and version pins only tighten.
    new-code  Google-docstring findings on lines changed against the merge-base.
    burndown  Rewrites ``ruff-burndown.toml`` from the current findings (shrink-only by default).

Only the standard library is used; ruff and mypy run as ``<this interpreter> -m ruff|mypy``.
"""

from __future__ import annotations

import argparse
import configparser
import dataclasses
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILES = (
    "pyproject.toml",
    "ruff.toml",
    "mypy.ini",
    "standards.toml",
    "ruff-burndown.toml",
    "requirements-dev.txt",
)
STATE_FILE = ".claude/state/standards-stop.json"
TRAILER = "Lint-Ratchet:"
_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# mypy keys whose new value can only make checking weaker.
_MYPY_LOOSENING = (
    "ignore_errors",
    "disable_error_code",
    "ignore_missing_imports",
    "follow_imports",
)


@dataclasses.dataclass(frozen=True)
class Config:
    """Per-repo settings from ``[tool.standards]``."""

    root: Path
    base: str = "origin/main"
    advisory: bool = True
    mypy_at_stop: bool = False
    mypy_args: tuple[str, ...] = ()
    mypy_paths: tuple[str, ...] = ("src",)
    protected: tuple[str, ...] = ()
    config_files: tuple[str, ...] = DEFAULT_CONFIG_FILES
    requirements: tuple[str, ...] = ("requirements-dev.txt",)
    burndown: str = "ruff-burndown.toml"
    new_code_select: tuple[str, ...] = ("D",)
    new_code_ignore: tuple[str, ...] = ("D105", "D107")

    @property
    def ruff_config(self) -> str:
        """The repo's main ruff config file."""
        return "ruff.toml" if (self.root / "ruff.toml").is_file() else "pyproject.toml"

    @property
    def mypy_config(self) -> str:
        """The repo's mypy config file."""
        return "mypy.ini" if (self.root / "mypy.ini").is_file() else "pyproject.toml"


def load_config(root: Path) -> Config:
    """Reads ``[tool.standards]`` from ``pyproject.toml``, else from ``standards.toml``.

    Args:
        root: Repository root.

    Returns:
        The repo settings, with defaults for missing keys.

    Raises:
        ValueError: If the table has an unknown key (a mistyped key must not silently default).
    """
    table: dict[str, Any] = {}
    for name in ("pyproject.toml", "standards.toml"):
        path = root / name
        if path.is_file():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            table = data.get("tool", {}).get("standards", {})
            if table:
                break
    known = {f.name for f in dataclasses.fields(Config)} - {"root"}
    unknown = set(table) - known
    if unknown:
        raise ValueError(f"unknown [tool.standards] keys: {sorted(unknown)}")
    values: dict[str, Any] = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
    return Config(root=root, **values)


# --------------------------------------------------------------------------- git and tools


def _run(args: list[str], cwd: Path, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    # Fixed argument lists, never a shell; git and this interpreter are trusted executables.
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def merge_base(cfg: Config) -> str | None:
    """Returns the merge-base of HEAD and the configured base, or None if it can't be found."""
    proc = _run(["git", "merge-base", "HEAD", cfg.base], cfg.root)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _is_protected(cfg: Config, path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in cfg.protected)


def changed_files(cfg: Config, base: str) -> list[str]:
    """Lists existing files changed against ``base`` (committed or not) plus untracked files.

    Args:
        cfg: Repo settings.
        base: Commit to compare with, normally the merge-base.

    Returns:
        Sorted repo-relative POSIX paths, without protected paths.
    """
    diff = _run(["git", "diff", "--name-only", "--no-renames", base], cfg.root).stdout.splitlines()
    new = _run(["git", "ls-files", "--others", "--exclude-standard"], cfg.root).stdout.splitlines()
    paths = {p.strip() for p in diff + new if p.strip()}
    return sorted(p for p in paths if (cfg.root / p).is_file() and not _is_protected(cfg, p))


def changed_lines(cfg: Config, base: str, path: str) -> set[int] | None:
    """Returns the line numbers of ``path`` changed against ``base``; None means every line."""
    tracked = _run(["git", "cat-file", "-e", f"{base}:{path}"], cfg.root).returncode == 0
    if not tracked:
        return None
    lines: set[int] = set()
    diff = _run(["git", "diff", "-U0", "--no-color", base, "--", path], cfg.root).stdout
    for line in diff.splitlines():
        match = _HUNK.match(line)
        if match:
            start, count = int(match.group(1)), int(match.group(2) or 1)
            lines.update(range(start, start + count))
    return lines


def _show(cfg: Config, base: str, path: str) -> str | None:
    proc = _run(["git", "show", f"{base}:{path}"], cfg.root)
    return proc.stdout if proc.returncode == 0 else None


def _rel(cfg: Config, filename: str) -> str:
    path = Path(filename)
    if path.is_absolute():
        path = path.resolve().relative_to(cfg.root.resolve())
    return path.as_posix()


def ruff_findings(
    cfg: Config, files: list[str], extra: list[str] | None = None
) -> list[dict[str, Any]]:
    """Runs ruff with the repo config on ``files`` (all files if empty) and returns its findings."""
    args = [sys.executable, "-m", "ruff", "check", "--output-format", "json", "--exit-zero"]
    proc = _run([*args, *(extra or []), *(files or ["."])], cfg.root)
    if proc.returncode != 0:
        raise RuntimeError(f"ruff failed: {proc.stderr.strip()}")
    findings: list[dict[str, Any]] = json.loads(proc.stdout or "[]")
    for finding in findings:
        finding["path"] = _rel(cfg, finding["filename"])
        finding["code"] = finding.get("code") or "syntax-error"
    return findings


def _format(finding: dict[str, Any]) -> str:
    row = finding["location"]["row"]
    return f"{finding['path']}:{row}: {finding['code']} {finding['message']}"


# --------------------------------------------------------------------------- new-code


def new_code(cfg: Config, base: str, files: list[str]) -> list[str]:
    """Returns Google-docstring findings that sit on lines changed against ``base``.

    Args:
        cfg: Repo settings.
        base: Commit to compare with, normally the merge-base.
        files: Changed files to check; non-Python files are skipped.

    Returns:
        One formatted line per finding.
    """
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return []
    extra = [
        "--select",
        ",".join(cfg.new_code_select),
        "--ignore",
        ",".join(cfg.new_code_ignore),
        "--config",
        'lint.pydocstyle.convention = "google"',
    ]
    result = []
    lines_by_file = {f: changed_lines(cfg, base, f) for f in py}
    for finding in ruff_findings(cfg, py, extra):
        lines = lines_by_file.get(finding["path"])
        if lines is None or finding["location"]["row"] in lines:
            result.append(_format(finding))
    return result


# --------------------------------------------------------------------------- ratchet


def _toml(text: str | None) -> dict[str, Any]:
    return tomllib.loads(text) if text else {}


def _burndown_entries(data: dict[str, Any]) -> set[tuple[str, str]]:
    table = data.get("lint", {}).get("per-file-ignores", {})
    return {(path, rule) for path, rules in table.items() for rule in rules}


def _ruff_section(name: str, data: dict[str, Any]) -> dict[str, Any]:
    section = data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
    lint = dict(section.get("lint", {}))
    for key in ("line-length", "exclude", "extend-exclude"):
        if key in section:
            lint[f"top.{key}"] = section[key]
    return lint


def _ruff_problems(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    def rules(section: dict[str, Any], *keys: str) -> set[str]:
        return {rule for key in keys for rule in section.get(key, [])}

    def pairs(section: dict[str, Any]) -> set[tuple[str, str]]:
        tables = [section.get("per-file-ignores", {}), section.get("extend-per-file-ignores", {})]
        return {(path, rule) for table in tables for path, rs in table.items() for rule in rs}

    dropped = rules(old, "select", "extend-select") - rules(new, "select", "extend-select")
    ignored = rules(new, "ignore", "extend-ignore") - rules(old, "ignore", "extend-ignore")
    problems = [f"ruff rule no longer selected: {r}" for r in sorted(dropped)]
    problems += [f"ruff rule newly ignored: {r}" for r in sorted(ignored)]
    problems += [f"ruff per-file ignore added: {p} {r}" for p, r in sorted(pairs(new) - pairs(old))]
    excluded = set(new.get("top.exclude", [])) | set(new.get("top.extend-exclude", []))
    excluded -= set(old.get("top.exclude", [])) | set(old.get("top.extend-exclude", []))
    problems += [f"ruff path newly excluded: {p}" for p in sorted(excluded)]
    if new.get("top.line-length", 0) > old.get("top.line-length", 10**6):
        problems.append("ruff line-length increased")
    return problems


def _mypy_sections(name: str, text: str | None) -> dict[str, dict[str, str]]:
    if not text:
        return {}
    if name == "mypy.ini":
        parser = configparser.ConfigParser()
        parser.read_string(text)
        return {s: dict(parser[s]) for s in parser.sections()}
    data = _toml(text).get("tool", {}).get("mypy", {})
    sections = {"mypy": {k: json.dumps(v) for k, v in data.items() if k != "overrides"}}
    for override in data.get("overrides", []):
        module = override.get("module", "")
        key = ",".join(module) if isinstance(module, list) else module
        sections[f"mypy-{key}"] = {k: json.dumps(v) for k, v in override.items() if k != "module"}
    return sections


def _mypy_problems(old: dict[str, dict[str, str]], new: dict[str, dict[str, str]]) -> list[str]:
    problems = [f"mypy override added: [{s}]" for s in sorted(set(new) - set(old)) if s != "mypy"]
    for section in sorted(set(new) & set(old)):
        for key, value in new[section].items():
            before = old[section].get(key)
            if before == value:
                continue
            loosened = key in _MYPY_LOOSENING or (
                key.startswith(("disallow_", "warn_", "strict", "check_", "no_implicit"))
                and before is not None
                and value.strip().lower() in ("false", "0")
            )
            if loosened:
                problems.append(f"mypy setting loosened: [{section}] {key} = {value}")
    return problems


def _requirements(text: str | None) -> dict[str, str | None]:
    """Maps each requirement name to its exact ``==`` version, or None when it isn't pinned."""
    result: dict[str, str | None] = {}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQ_NAME.match(line)
        if not match:
            continue
        spec = line[match.end() :].split(";", 1)[0].strip()
        exact = spec.startswith("==") and not any(op in spec[2:] for op in "<>=!~*,")
        result[match.group(1).lower()] = spec[2:].strip() if exact else None
    return result


def _pin_problems(old: dict[str, str | None], new: dict[str, str | None]) -> list[str]:
    problems = []
    for name, version in sorted(new.items()):
        if version is None and old.get(name, "") is not None:
            problems.append(f"requirement not pinned with ==: {name}")
        elif version is not None and name in old and old[name] is not None and old[name] != version:
            problems.append(f"pinned version changed: {name} {old[name]} -> {version}")
    return problems


def ratchet_trailers(cfg: Config, base: str) -> list[str]:
    """Returns the ``Lint-Ratchet:`` reasons in commits between ``base`` and HEAD."""
    log = _run(["git", "log", "--format=%B", f"{base}..HEAD"], cfg.root).stdout
    return [line.split(":", 1)[1].strip() for line in log.splitlines() if line.startswith(TRAILER)]


def ratchet(cfg: Config, base: str) -> list[str]:
    """Lists every way the working tree loosens lint, type or pin config against ``base``.

    Args:
        cfg: Repo settings.
        base: Commit to compare with, normally the merge-base.

    Returns:
        One line per problem; empty when nothing was loosened.
    """

    def current(path: str) -> str | None:
        file = cfg.root / path
        return file.read_text(encoding="utf-8") if file.is_file() else None

    old_burn = _burndown_entries(_toml(_show(cfg, base, cfg.burndown)))
    new_burn = _burndown_entries(_toml(current(cfg.burndown)))
    problems = [f"burn-down entry added: {p} {r}" for p, r in sorted(new_burn - old_burn)]
    name = cfg.ruff_config
    old_ruff = _ruff_section(name, _toml(_show(cfg, base, name)))
    problems += _ruff_problems(old_ruff, _ruff_section(name, _toml(current(name))))
    name = cfg.mypy_config
    old_mypy = _mypy_sections(name, _show(cfg, base, name))
    problems += _mypy_problems(old_mypy, _mypy_sections(name, current(name)))
    for req in cfg.requirements:
        problems += _pin_problems(_requirements(_show(cfg, base, req)), _requirements(current(req)))
    return problems


# --------------------------------------------------------------------------- burndown


def _write_burndown(path: Path, entries: dict[str, set[str]]) -> None:
    lines = [
        "# Burn-down list: existing ruff findings, one entry per file and rule. Generated by",
        "# tools/standards/check_standards.py burndown; it may only shrink (the ratchet check",
        "# fails on new entries). Fix the code and remove entries; never add entries by hand.",
        "",
        "[lint.per-file-ignores]",
    ]
    lines += [
        f'"{path_}" = [{", ".join(map(json.dumps, sorted(rules)))}]'
        for path_, rules in sorted(entries.items())
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def burndown(cfg: Config, initial: bool) -> list[str]:
    """Rewrites the burn-down file from the current findings.

    Args:
        cfg: Repo settings.
        initial: Allow new entries. Only for the first generation or a tier promotion.

    Returns:
        Entries that would have been added; when ``initial`` is False and this is non-empty, the
        file is left unchanged.
    """
    path = cfg.root / cfg.burndown
    previous = path.read_text(encoding="utf-8") if path.is_file() else None

    def restore() -> None:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")

    entries: dict[str, set[str]] = {}
    try:
        # A per-file entry can turn an existing noqa comment for the same rule into an
        # "unused noqa" (RUF100) finding, so repeat until the list reaches a fixed point.
        for _ in range(3):
            _write_burndown(path, entries)
            findings = ruff_findings(cfg, [])
            if not findings:
                break
            for finding in findings:
                entries.setdefault(finding["path"], set()).add(finding["code"])
    except Exception:
        restore()
        raise
    old = _burndown_entries(_toml(previous))
    added = sorted(f"{p} {r}" for p, rules in entries.items() for r in rules if (p, r) not in old)
    if added and not initial:
        restore()
        return added
    _write_burndown(path, entries)
    return [] if not initial else added


# --------------------------------------------------------------------------- stop


def _fingerprint(cfg: Config, base: str, files: list[str]) -> str:
    digest = hashlib.sha256(base.encode())
    for path in files:
        digest.update(path.encode())
        digest.update(hashlib.sha256((cfg.root / path).read_bytes()).digest())
    return digest.hexdigest()


def _mypy(cfg: Config) -> list[str]:
    proc = _run([sys.executable, "-m", "mypy", *cfg.mypy_args], cfg.root, timeout=45)
    return [line for line in proc.stdout.splitlines() if ": error:" in line]


def stop(cfg: Config) -> tuple[str, bool]:
    """Runs the Stop sequence (plan decision 11) and returns the report.

    Args:
        cfg: Repo settings.

    Returns:
        The report text (empty when there was nothing to do) and whether it contains findings.
    """
    base = merge_base(cfg)
    if base is None:
        return f"standards: no merge-base with {cfg.base}; checks skipped.", False
    relevant = [f for f in changed_files(cfg, base) if f.endswith(".py") or f in cfg.config_files]
    if not relevant:
        return "", False
    state = cfg.root / STATE_FILE
    fingerprint = _fingerprint(cfg, base, relevant)
    try:
        if json.loads(state.read_text(encoding="utf-8")).get("fingerprint") == fingerprint:
            return "", False
    except (OSError, ValueError):
        pass

    py = [f for f in relevant if f.endswith(".py")]
    sections: list[tuple[str, list[str]]] = []
    if py:
        sections.append(("ruff", [_format(f) for f in ruff_findings(cfg, py)]))
        sections.append(("new code (Google docstrings, changed lines)", new_code(cfg, base, py)))
    if any(f in cfg.config_files for f in relevant):
        problems = ratchet(cfg, base)
        if problems and ratchet_trailers(cfg, base):
            problems = []
        sections.append(("ratchet", problems))
    if cfg.mypy_at_stop and any(f.startswith(tuple(f"{p}/" for p in cfg.mypy_paths)) for f in py):
        sections.append(("mypy", _mypy(cfg)))

    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    body = [
        f"{title}:\n" + "\n".join(f"  {line}" for line in lines[:40])
        for title, lines in sections
        if lines
    ]
    if not body:
        return "", False
    return "Coding standards findings in changed files:\n" + "\n".join(body), True


def _hook_stop(cfg: Config) -> None:
    """Runs ``stop`` for a Claude Code Stop hook: reads the event, writes the hook response."""
    try:
        # utf-8-sig: a PowerShell wrapper prefixes piped input with a UTF-8 BOM.
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except (OSError, ValueError):
        event = {}
    try:
        report, found = stop(cfg)
    except Exception as exc:  # isolation point: a broken check must never trap the agent
        report, found = f"standards: Stop check could not run ({exc}); allowing stop.", False
    if not report:
        return
    if found and not cfg.advisory and not event.get("stop_hook_active"):
        sys.stdout.write(json.dumps({"decision": "block", "reason": report}))
    else:
        sys.stdout.write(json.dumps({"systemMessage": report}))


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 when clean, 1 when there are findings or problems.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    stop_cmd = sub.add_parser("stop", help="Stop-hook sequence")
    stop_cmd.add_argument(
        "--hook", action="store_true", help="read the hook event, write the hook response"
    )
    sub.add_parser("ratchet", help="fail if lint/type config or pins were loosened")
    sub.add_parser("new-code", help="Google-docstring findings on changed lines")
    burn = sub.add_parser("burndown", help="rewrite ruff-burndown.toml (shrink-only)")
    burn.add_argument("--initial", action="store_true", help="allow new entries")
    args = parser.parse_args(argv)
    cfg = load_config(args.root.resolve())
    out = sys.stdout

    if args.command == "stop":
        if args.hook:
            _hook_stop(cfg)
            return 0
        report, found = stop(cfg)
        out.write(report + ("\n" if report else ""))
        return 1 if found else 0
    if args.command == "burndown":
        added = burndown(cfg, args.initial)
        if added and not args.initial:
            out.write("burn-down not updated; it would add entries ")
            out.write("(use --initial only for a ratchet step):\n")
            out.write("".join(f"  {a}\n" for a in added))
            return 1
        out.write(f"wrote {cfg.burndown}\n")
        return 0

    base = merge_base(cfg)
    if base is None:
        out.write(f"no merge-base with {cfg.base}; fetch it first (CI: fetch-depth: 0)\n")
        return 1
    if args.command == "ratchet":
        problems = ratchet(cfg, base)
        reasons = ratchet_trailers(cfg, base)
        if problems and reasons:
            out.write("loosening accepted by Lint-Ratchet trailer: " + "; ".join(reasons) + "\n")
            problems = []
        out.write("".join(f"{p}\n" for p in problems))
        return 1 if problems else 0
    findings = new_code(cfg, base, changed_files(cfg, base))
    out.write("".join(f"{f}\n" for f in findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
