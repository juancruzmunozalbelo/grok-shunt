#!/usr/bin/env python3
"""PreToolUse gate: block untargeted large-file reads on the frontier session.

Fail-open on parse errors, missing files, non-regular files, and timeouts
(the harness also fail-opens). Explicit deny JSON is the only block.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

DEFAULT_MIN_LINES = 350
DEFAULT_MIN_BYTES = 65536
DEFAULT_MAX_LIMIT = 120
READERS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg"}
PREFIXES = {"command", "env", "nice", "time", "exec", "nohup"}
PIPE_MARKERS = ("|", "`", "$(", "<(")
SHUNT_MARKERS = (
    "bulk-read",
    "code-write",
    "check-read.py",
    "shunt_worker.py",
    "install-user-hook",
    "bench_tokens.py",
)
PATHISH = re.compile(r"(?:~|/|\./|\.\./)[^\s'\"();|&<>]+")


def allow() -> None:
    sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
    raise SystemExit(0)


def deny(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "deny", "reason": reason}) + "\n")
    raise SystemExit(0)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n > 0 else default


def min_lines() -> int:
    return env_int("SHUNT_MIN_LINES", DEFAULT_MIN_LINES)


def min_bytes() -> int:
    return env_int("SHUNT_MIN_BYTES", DEFAULT_MIN_BYTES)


def max_limit() -> int:
    return env_int("SHUNT_MAX_LIMIT", DEFAULT_MAX_LIMIT)


def disabled() -> bool:
    raw = os.environ.get("SHUNT_DISABLE", "").strip().lower()
    return raw in {"1", "true"}


def file_stats(path: str) -> tuple[int, int] | None:
    """(lines, bytes) for a regular file. None = skip (fail-open)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    size = int(st.st_size)
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    n = 0
    try:
        while True:
            try:
                chunk = os.read(fd, 1 << 20)
            except BlockingIOError:
                break
            if not chunk:
                break
            n += chunk.count(b"\n")
    finally:
        os.close(fd)
    return n, size


def resolve(path: str, cwd: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(cwd or os.getcwd(), path))


def strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def split_segments(command: str) -> list[str]:
    segs: list[str] = []
    buf: list[str] = []
    quote = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in {"'", '"'}:
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "&" and i + 1 < len(command) and command[i + 1] == "&":
            segs.append("".join(buf).strip())
            buf = []
            i += 2
            continue
        if c in {";", "\n"}:
            segs.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf).strip())
    return [s for s in segs if s]


def strip_prefixes(parts: list[str]) -> list[str]:
    i = 0
    while i < len(parts):
        base = os.path.basename(strip_quotes(parts[i]))
        if base not in PREFIXES:
            break
        i += 1
        if base == "env":
            while (
                i < len(parts)
                and "=" in parts[i]
                and not parts[i].startswith("-")
            ):
                i += 1
    return parts[i:]


def extract_file_args(tokens: list[str]) -> list[str]:
    files: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in {">", ">>", "2>", "&>"}:
            skip_next = True
            continue
        if tok == "<":
            continue
        if tok == "--":
            continue
        if tok.startswith("-"):
            continue
        files.append(strip_quotes(tok))
    return files


def is_shunt_invocation(command: str) -> bool:
    return any(marker in command for marker in SHUNT_MARKERS)


def interpreter_dump_paths(parts: list[str], segment: str) -> list[str]:
    """Paths named inside python -c / perl -e / ruby -e / node -e."""
    prog = os.path.basename(strip_quotes(parts[0]))
    if prog.startswith("python"):
        flag = "-c"
    elif prog in {"perl", "ruby", "node"}:
        flag = "-e"
    else:
        return []
    if flag not in parts:
        return []
    skip = {strip_quotes(parts[0]), os.path.abspath(strip_quotes(parts[0]))}
    found: list[str] = []
    for m in PATHISH.finditer(segment):
        raw = m.group(0)
        if raw in skip or os.path.basename(raw) == prog:
            continue
        found.append(raw)
    return found


def bash_read_paths(command: str) -> list[str]:
    """Paths this command would dump in full. Empty = not a dump (allow)."""
    cmd = command.strip()
    if not cmd:
        return []
    if any(m in cmd for m in PIPE_MARKERS):
        return []
    if is_shunt_invocation(cmd):
        return []
    paths: list[str] = []
    for segment in split_segments(cmd):
        parts = segment.split()
        if not parts:
            continue
        parts = strip_prefixes(parts)
        if not parts:
            continue
        prog = os.path.basename(strip_quotes(parts[0]))
        paths.extend(interpreter_dump_paths(parts, segment))
        if prog in READERS:
            paths.extend(extract_file_args(parts[1:]))
    return paths


def bulk_read_script() -> str:
    return str(Path(__file__).parent.parent / "scripts" / "bulk-read")


def deny_reason(path: str, lines: int, size: int, lines_n: int, bytes_n: int) -> str:
    script = bulk_read_script()
    return (
        f"shunt: {path} is {lines} lines / {size} bytes "
        f"(thresholds {lines_n} lines, {bytes_n} bytes). "
        "Do not Read or cat it. Run this command and keep only its stdout:\n"
        f'python3 {script} --question "<the user question>" --paths {path}\n'
        "Use offset/limit only for a targeted edit window "
        f"(limit ≤ {max_limit()}). Skill: bulk-reader."
    )


def targeted_window(inp: dict) -> bool:
    limit = inp.get("limit")
    if limit is None:
        return False
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return False
    return 0 < n <= max_limit()


def over_threshold(path: str, cwd: str) -> tuple[str, int, int] | None:
    full = resolve(path, cwd)
    stats = file_stats(full)
    if stats is None:
        return None
    lines, size = stats
    if size > min_bytes() or lines > min_lines():
        return full, lines, size
    return None


def decide(payload: dict) -> None:
    if disabled():
        allow()

    tool = payload.get("toolName") or ""
    inp = payload.get("toolInput") or {}
    cwd = payload.get("cwd") or payload.get("workspaceRoot") or os.getcwd()
    hits: list[tuple[str, int, int]] = []

    if tool in {"read_file", "Read"}:
        if targeted_window(inp):
            allow()
        path = inp.get("target_file") or inp.get("path")
        if not path:
            allow()
        hit = over_threshold(str(path), str(cwd))
        if hit:
            hits.append(hit)
    elif tool in {"grep", "Grep"}:
        path = inp.get("path")
        if not path:
            allow()
        hit = over_threshold(str(path), str(cwd))
        if hit:
            hits.append(hit)
    elif tool in {"run_terminal_command", "Bash", "run_terminal_cmd"}:
        for path in bash_read_paths(inp.get("command") or ""):
            hit = over_threshold(path, str(cwd))
            if hit:
                hits.append(hit)
    else:
        allow()

    if not hits:
        allow()
    full, lines, size = hits[0]
    deny(deny_reason(full, lines, size, min_lines(), min_bytes()))


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
