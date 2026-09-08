#!/usr/bin/env python3
"""PreToolUse gate: block untargeted large-file reads on the frontier session.

Fail-open on parse errors, missing files, and timeouts (the harness also
fail-opens). Explicit deny JSON is the only block. See README.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_MIN_LINES = 350
READERS = {"cat", "less", "more"}
WINDOWED = {"head", "tail"}


def allow() -> None:
    sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
    raise SystemExit(0)


def deny(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "deny", "reason": reason}) + "\n")
    raise SystemExit(0)


def min_lines() -> int:
    raw = os.environ.get("SHUNT_MIN_LINES", str(DEFAULT_MIN_LINES))
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MIN_LINES
    return n if n > 0 else DEFAULT_MIN_LINES


def line_count(path: str) -> int | None:
    try:
        n = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                n += chunk.count(b"\n")
        return n
    except OSError:
        return None


def resolve(path: str, cwd: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(cwd or os.getcwd(), path))


def strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def bash_full_read_path(command: str) -> str | None:
    """Return a path when the command is an untargeted full-file dump.

    `cat` / `less` / `more` of a single file, no pipes. `head`/`tail` pass:
    they already window. Any `|`, `&&`, `;`, redirect, or substitution passes.
    """
    cmd = command.strip()
    if not cmd:
        return None
    if any(sep in cmd for sep in ("|", "&&", ";", "`", "$(", ">", "<", "\n")):
        return None
    parts = cmd.split()
    if not parts:
        return None
    prog = os.path.basename(parts[0])
    if prog in WINDOWED:
        return None
    if prog not in READERS:
        return None
    args = [p for p in parts[1:] if not p.startswith("-")]
    if len(args) != 1:
        return None
    return strip_quotes(args[0])


def bulk_read_script() -> str:
    # Keep the invoked path (symlink under ~/.grok/plugins/shunt when installed).
    return str(Path(__file__).parent.parent / "scripts" / "bulk-read")


def deny_reason(path: str, n: int, threshold: int) -> str:
    script = bulk_read_script()
    return (
        f"shunt: {path} is {n} lines (threshold {threshold}). "
        "Do not Read or cat it. Run this command and keep only its stdout:\n"
        f'python3 {script} --question "<the user question>" --paths {path}\n'
        "Use offset/limit only for a targeted edit window. Skill: bulk-reader."
    )


def decide(payload: dict) -> None:
    if os.environ.get("SHUNT_DISABLE"):
        allow()
    if payload.get("subagentType"):
        allow()

    tool = payload.get("toolName") or ""
    inp = payload.get("toolInput") or {}
    cwd = payload.get("cwd") or payload.get("workspaceRoot") or os.getcwd()
    threshold = min_lines()
    path = None

    if tool in {"read_file", "Read"}:
        if inp.get("offset") is not None or inp.get("limit") is not None:
            allow()
        path = inp.get("target_file") or inp.get("path")
    elif tool in {"run_terminal_command", "Bash", "run_terminal_cmd"}:
        path = bash_full_read_path(inp.get("command") or "")
        if path is None:
            allow()
    else:
        allow()

    if not path:
        allow()
    full = resolve(str(path), str(cwd))
    n = line_count(full)
    if n is None:
        allow()
    if n <= threshold:
        allow()
    deny(deny_reason(full, n, threshold))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        allow()
    if not isinstance(payload, dict):
        allow()
    decide(payload)


if __name__ == "__main__":
    main()
