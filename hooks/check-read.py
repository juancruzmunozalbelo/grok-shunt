#!/usr/bin/env python3
"""Gate large-file dumps without a deny turn.

PreToolUse allows. PostToolUse replaces the model's copy of the tool
result with MiniMax bullets (or a stub). Fail-open: a hook error leaves
the original result. tgrep/grep/rg are searches, not dumps.
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
DUMPERS = {"cat", "less", "more"}
REDUCERS = {
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "tgrep",
    "awk",
    "gawk",
    "sed",
    "cut",
    "wc",
    "sort",
    "uniq",
    "jq",
    "tr",
    "head",
    "tail",
}
WORKER_SYSTEM = (
    "You are a precise code analyst. Structured bullets only. "
    "No greetings, no full file body. Quote at most 8 lines per point."
)
PREFIXES = {"command", "env", "nice", "time", "exec", "nohup"}
SUBST_MARKERS = ("`", "$(", "<(")
SHUNT_MARKERS = {
    "bulk-read",
    "code-write",
    "check-read.py",
    "shunt_worker.py",
    "install-user-hook",
    "bench_tokens.py",
}
PATHISH = re.compile(r"(?:~|/|\./|\.\./)[^\s'\"();|&<>]+")
QUOTED = re.compile(r"""(['"])([^'"]+)\1""")


def allow() -> None:
    sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
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


def split_on_pipe(command: str) -> list[str]:
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
        if c == "|" and not (
            i + 1 < len(command) and command[i + 1] in {"|", "&"}
        ):
            segs.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf).strip())
    return [s for s in segs if s]


def pipeline_filters(segment: str) -> bool:
    """True when the last pipe command shrinks stdout (grep, head, wc, …)."""
    pieces = split_on_pipe(segment)
    if len(pieces) < 2:
        return False
    parts = strip_prefixes(pieces[-1].split())
    if not parts:
        return False
    return os.path.basename(strip_quotes(parts[0])) in REDUCERS


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
    for segment in split_segments(command):
        for piece in split_on_pipe(segment):
            for tok in piece.split():
                if os.path.basename(strip_quotes(tok)) in SHUNT_MARKERS:
                    return True
    return False


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
    for m in QUOTED.finditer(segment):
        raw = m.group(2).strip()
        if not raw or raw in skip or "(" in raw or ")" in raw:
            continue
        found.append(raw)
    return found


def bash_read_paths(command: str) -> list[str]:
    """Paths this command would dump in full. Empty = not a dump (allow)."""
    cmd = command.strip()
    if not cmd:
        return []
    if any(m in cmd for m in SUBST_MARKERS):
        return []
    if is_shunt_invocation(cmd):
        return []
    paths: list[str] = []
    for segment in split_segments(cmd):
        if pipeline_filters(segment):
            continue
        for piece in split_on_pipe(segment):
            parts = piece.split()
            if not parts:
                continue
            parts = strip_prefixes(parts)
            if not parts:
                continue
            prog = os.path.basename(strip_quotes(parts[0]))
            paths.extend(interpreter_dump_paths(parts, piece))
            if prog in DUMPERS:
                paths.extend(extract_file_args(parts[1:]))
    return paths


def bulk_read_script() -> str:
    return str(Path(__file__).parent.parent / "scripts" / "bulk-read")


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


def dump_hits(payload: dict) -> list[tuple[str, int, int]]:
    """Paths this call would dump in full. Empty = search/window/small."""
    tool = payload.get("toolName") or ""
    inp = payload.get("toolInput") or {}
    cwd = payload.get("cwd") or payload.get("workspaceRoot") or os.getcwd()
    hits: list[tuple[str, int, int]] = []
    if tool in {"read_file", "Read"}:
        if targeted_window(inp):
            return []
        path = inp.get("target_file") or inp.get("path")
        if not path:
            return []
        hit = over_threshold(str(path), str(cwd))
        if hit:
            hits.append(hit)
    elif tool in {"run_terminal_command", "Bash", "run_terminal_cmd"}:
        for path in bash_read_paths(inp.get("command") or ""):
            hit = over_threshold(path, str(cwd))
            if hit:
                hits.append(hit)
    return hits


def is_post(payload: dict) -> bool:
    raw = (
        os.environ.get("GROK_HOOK_EVENT")
        or payload.get("hookEventName")
        or payload.get("hook_event_name")
        or ""
    )
    return "post" in str(raw).lower()


def noop() -> None:
    raise SystemExit(0)


def question_from(payload: dict) -> str:
    for key in ("userPrompt", "prompt", "lastUserMessage"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:2000]
    return "What does the user need from this file?"


def worker_bullets(path: str, question: str) -> str | None:
    fake = os.environ.get("SHUNT_FAKE_BULLETS")
    if fake is not None:
        return fake
    try:
        scripts = Path(__file__).resolve().parent.parent / "scripts"
        sys.path.insert(0, str(scripts))
        from shunt_worker import chat, pack_files, report_usage  # noqa: WPS433

        packed = pack_files([path])
        text, usage = chat(
            [
                {"role": "system", "content": WORKER_SYSTEM},
                {
                    "role": "user",
                    "content": f"Question:\n{question}\n\nFiles:\n{packed}",
                },
            ]
        )
        report_usage(usage)
        text = (text or "").strip()
        return text or None
    except (Exception, SystemExit):
        return None


def omitted_text(path: str, lines: int, size: int, bullets: str | None) -> str:
    header = (
        f"shunt: omitted {lines} lines / {size} bytes from the frontier "
        f"({path}).\n"
    )
    if bullets:
        return header + "\n" + bullets.strip() + "\n"
    script = bulk_read_script()
    return (
        header
        + "Worker unavailable. Run:\n"
        f'python3 {script} --question "What does the user need from this file?" '
        f"--paths {path}\n"
    )


def rewrite_tool_result(original: object, text: str) -> object:
    if original is None or isinstance(original, str):
        return text
    if not isinstance(original, dict):
        return text
    out = dict(original)
    kind = out.get("type")
    if kind == "ReadFile":
        fc = dict(out.get("FileContent") or {})
        fc["content"] = text
        fc["content_concise"] = text
        fc["raw_output"] = text
        fc["total_lines"] = text.count("\n") or 1
        out["FileContent"] = fc
        out.pop("FileNotFound", None)
        return out
    if kind == "Bash" or "output_for_prompt" in out:
        encoded = list(text.encode("utf-8"))
        out["output_for_prompt"] = text
        out["output"] = encoded
        out["truncated"] = False
        if "total_bytes" in out:
            out["total_bytes"] = len(encoded)
        out["exit_code"] = 0
        return out
    return text


def post_decide(payload: dict) -> None:
    tr = payload.get("toolResult") or payload.get("tool_response")
    if isinstance(tr, dict) and tr.get("FileNotFound"):
        noop()
    hits = dump_hits(payload)
    if not hits:
        noop()
    full, lines, size = hits[0]
    bullets = worker_bullets(full, question_from(payload))
    text = omitted_text(full, lines, size, bullets)
    updated = rewrite_tool_result(tr, text)
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": updated,
                }
            }
        )
        + "\n"
    )
    raise SystemExit(0)


def decide(payload: dict) -> None:
    if disabled():
        if is_post(payload):
            noop()
        allow()
    if is_post(payload):
        post_decide(payload)
        return
    allow()


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
