"""PreToolUse guard (Production Hardening §L dev harness): deny access to secret files.

Blocks a dev agent from reading, editing or printing `.env` (it holds the live API keys),
private keys and credential files. Two kinds of tool call are checked:

- File tools (Read, Edit, Write, MultiEdit, NotebookEdit): the target path's basename,
  as given and after resolving symlinks, against the protected names and suffixes.
- Shell tools (Bash, PowerShell): the command text, for any mention of an env file.

Template variants (`.env.example`, `.env.template`, ...) are allowed so the committed
templates stay editable.

Contract: print a PreToolUse `permissionDecision: "deny"` to block a protected target;
stay silent (exit 0) to allow. Fails closed: if the event can't be parsed, a file tool
names no path, a shell tool names no command, or the guard itself errors, the call is
blocked (exit 2, reason on stderr). Stdlib only, so any `python` on PATH runs it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

# Allowed template variants of an env file (never carry real secrets).
_ENV_ALLOW_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults")

# Exact protected basenames.
_PROTECTED_NAMES = {
    ".env",
    ".git-credentials",
    ".pgpass",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "credentials",
    "credentials.json",
}

# Protected filename suffixes (private keys, certificate material, keystores).
_PROTECTED_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")

_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
_SHELL_TOOLS = {"Bash", "PowerShell"}

# Tool-input keys that name the target file.
_PATH_KEYS = ("file_path", "notebook_path", "path")

# `.env`, optionally with dotted suffixes, not followed by a word char, dot or dash.
_ENV_IN_COMMAND = re.compile(r"(?i)\.env((?:\.[\w-]+)*)(?![\w.\-])")

# Exit code that makes Claude Code treat a PreToolUse hook as a blocking error.
_BLOCK_EXIT = 2


def is_protected_name(path: str) -> bool:
    """Whether a path's basename marks it as a secret or credential file.

    Args:
        path: A file path, with either separator.

    Returns:
        True for `.env`, a non-template `.env.*`, and key or credential files.
    """
    low = os.path.basename(path.replace("\\", "/")).strip().lower()
    if low == ".env":
        return True
    if low.startswith(".env."):
        return not low.endswith(_ENV_ALLOW_SUFFIXES)
    if low in _PROTECTED_NAMES or low.endswith(_PROTECTED_SUFFIXES):
        return True
    return ("service-account" in low or low.startswith("secret")) and low.endswith(".json")


def is_protected(path: str) -> bool:
    """Whether a path, as given or after resolving symlinks, is protected.

    Args:
        path: A file path; it need not exist.

    Returns:
        True when the path or its symlink target is a protected file.
    """
    if is_protected_name(path):
        return True
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return resolved != path and is_protected_name(resolved)


def command_names_env(command: str) -> bool:
    """Whether a shell command mentions a non-template env file.

    Args:
        command: The shell command text.

    Returns:
        True when the command names `.env` or a non-template `.env.*`.
    """
    for match in _ENV_IN_COMMAND.finditer(command):
        if not match.group(1).lower().endswith(_ENV_ALLOW_SUFFIXES):
            return True
    return False


def target_path(tool_input: dict[str, Any]) -> str | None:
    """The file a file-tool call targets.

    Args:
        tool_input: The event's `tool_input` object.

    Returns:
        The first non-empty path field, or None when the call names none.
    """
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _block(reason: str) -> None:
    """Block the tool call as a hook error (fail closed)."""
    sys.stderr.write(f"Blocked by dev-harness (§L): {reason}\n")
    sys.exit(_BLOCK_EXIT)


def _deny(reason: str) -> None:
    """Deny the tool call with a reason the agent sees."""
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Blocked by dev-harness (§L): {reason}",
                }
            }
        )
    )
    sys.stdout.flush()


def main() -> None:
    """Runs the hook."""
    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        _block("the hook event could not be parsed, so the target is unknown.")
        return
    if not isinstance(event, dict):
        _block("the hook event is not a JSON object.")
        return
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool in _SHELL_TOOLS:
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            _block(f"the {tool} call names no command.")
            return
        if command_names_env(command):
            _deny(
                "the command names .env, which holds live API keys. Use .env.example or a "
                "placeholder, or ask the operator."
            )
        return

    path = target_path(tool_input)
    if path is None:
        if tool in _FILE_TOOLS:
            _block(f"the {tool} call names no target path.")
        return
    if is_protected(path):
        _deny(
            f"'{os.path.basename(path)}' is a protected secret or credential file, so "
            f"{tool or 'this tool'} may not touch it. Edit it by hand, or use a template "
            "variant such as .env.example."
        )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # isolation point: any guard failure blocks rather than allows
        _block(f"the guard failed ({type(exc).__name__}: {exc}).")
