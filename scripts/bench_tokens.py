#!/usr/bin/env python3
"""A/B token count: direct Read vs shunt (frontier grok-4.6 vs worker).

Usage:
  python3 scripts/bench_tokens.py
  python3 scripts/bench_tokens.py --lines 2000 --file /tmp/shunt-bench/big.py

Needs `grok` on PATH. Does not print API keys.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROMPT = """Read {path} with the Read tool on the WHOLE file (no offset, no limit).
If a hook denies the Read, immediately spawn_subagent with subagent_type "shunt:bulk-reader"
and prompt: "List the first three function names in {path}, one per line."
Reply with those three names, one per line, nothing else.
"""


def grok_json(
    prompt: str,
    cwd: str,
    env: dict,
    max_turns: int,
    tools: str,
    leader_socket: str,
) -> dict:
    cmd = [
        "grok",
        "--yolo",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--cwd",
        cwd,
        "--tools",
        tools,
        "--leader-socket",
        leader_socket,
        "-p",
        prompt,
    ]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    raw = proc.stdout.strip()
    if not raw:
        raise SystemExit(
            f"grok produced no stdout (exit {proc.returncode})\n{proc.stderr[-2000:]}"
        )
    # json format is one object; if NDJSON sneaks in, take the last object
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        last = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
        if last is None:
            raise SystemExit(f"could not parse grok json:\n{raw[-1500:]}")
        return last


def frontier_tokens(result: dict, frontier: str = "grok-4.6") -> dict[str, int]:
    usage = result.get("usage") or {}
    models = result.get("modelUsage") or {}
    by: dict[str, dict] = {}
    for name, row in models.items():
        if not isinstance(row, dict):
            continue
        by[name] = {
            "input": int(row.get("inputTokens") or 0)
            + int(row.get("cacheReadInputTokens") or 0)
            + int(row.get("cacheCreationInputTokens") or 0),
            "output": int(row.get("outputTokens") or 0),
            "calls": int(row.get("modelCalls") or 0),
        }
    f_in = sum(v["input"] for k, v in by.items() if k.startswith(frontier) or k == frontier)
    w_in = sum(v["input"] for k, v in by.items() if not (k.startswith(frontier) or k == frontier))
    return {
        "total_input": int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0),
        "total_output": int(usage.get("output_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
        "frontier_input": f_in,
        "worker_input": w_in,
        "models": by,  # type: ignore[dict-item]
        "turns": int(result.get("num_turns") or 0),
        "text": (result.get("text") or "")[:200],
        "denied": "Hook denied" in (result.get("text") or "")
        or "shunt:" in json.dumps(result)[:5000],
        "session_id": result.get("sessionId") or result.get("session_id"),
    }


def find_session(session_id: str | None) -> Path | None:
    if not session_id:
        return None
    root = Path.home() / ".grok" / "sessions"
    hits = list(root.rglob(session_id))
    dirs = [h for h in hits if h.is_dir() and (h / "signals.json").exists()]
    return dirs[0] if dirs else None


def parent_payload(session: Path | None) -> dict:
    out = {"context_tokens": None, "max_tool_result": 0, "file_in_parent": False, "hook_denied": False}
    if session is None:
        return out
    sig_path = session / "signals.json"
    if sig_path.exists():
        sig = json.loads(sig_path.read_text())
        out["context_tokens"] = sig.get("contextTokensUsed")
        out["models"] = sig.get("modelsUsed")
    hist = session / "chat_history.jsonl"
    if not hist.exists():
        return out
    for line in hist.read_text().splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "tool_result":
            continue
        body = obj.get("content") or ""
        out["max_tool_result"] = max(out["max_tool_result"], len(body))
        if "Hook denied:" in body and "shunt:" in body:
            out["hook_denied"] = True
        if "def f50():" in body or "def f100():" in body:
            out["file_in_parent"] = True
    return out


def make_file(path: Path, n_fns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_fns):
            fh.write(f"def f{i}():\n    return {i}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lines", type=int, default=2000, help="function pairs (~2 lines each)")
    ap.add_argument("--file", default="")
    ap.add_argument("--max-turns", type=int, default=8)
    args = ap.parse_args()

    tmp = Path(args.file) if args.file else Path(tempfile.mkdtemp(prefix="shunt-bench-")) / "big.py"
    make_file(tmp, args.lines)
    lines = sum(1 for _ in tmp.open())
    cwd = str(tmp.parent)
    prompt = PROMPT.format(path=str(tmp))
    base_env = os.environ.copy()
    base_env.pop("SHUNT_DISABLE", None)

    print(f"fixture {tmp}  lines={lines}", file=sys.stderr)
    sock_dir = Path(tempfile.mkdtemp(prefix="shunt-sock-"))
    print("running SHUNT first (isolated leader)…", file=sys.stderr)
    env_on = base_env.copy()
    env_on.pop("SHUNT_DISABLE", None)
    on = grok_json(
        prompt,
        cwd,
        env_on,
        args.max_turns,
        "read_file,spawn_subagent",
        str(sock_dir / "on.sock"),
    )
    s = frontier_tokens(on)
    s_pay = parent_payload(find_session(s.get("session_id")))  # type: ignore[arg-type]

    print("running DIRECT (SHUNT_DISABLE=1, isolated leader)…", file=sys.stderr)
    env_off = base_env.copy()
    env_off["SHUNT_DISABLE"] = "1"
    direct = grok_json(
        prompt,
        cwd,
        env_off,
        min(args.max_turns, 3),
        "read_file",
        str(sock_dir / "off.sock"),
    )
    d = frontier_tokens(direct)
    d_pay = parent_payload(find_session(d.get("session_id")))  # type: ignore[arg-type]

    def row(label: str, t: dict) -> None:
        print(
            f"{label:10}  frontier_in={t['frontier_input']:<8}  "
            f"worker_in={t['worker_input']:<8}  total={t['total']:<8}  "
            f"turns={t['turns']}  models={list((t['models'] or {}).keys())}"
        )

    print()
    row("direct", d)
    row("shunt", s)
    print()
    print("parent window (signals.json)")
    print(
        f"direct  context_tokens={d_pay.get('context_tokens')}  "
        f"max_tool_result={d_pay.get('max_tool_result')}  "
        f"file_in_parent={d_pay.get('file_in_parent')}  hook_denied={d_pay.get('hook_denied')}"
    )
    print(
        f"shunt   context_tokens={s_pay.get('context_tokens')}  "
        f"max_tool_result={s_pay.get('max_tool_result')}  "
        f"file_in_parent={s_pay.get('file_in_parent')}  hook_denied={s_pay.get('hook_denied')}"
    )
    saved = d["frontier_input"] - s["frontier_input"]
    pct = (100.0 * saved / d["frontier_input"]) if d["frontier_input"] else 0.0
    print()
    print(f"ledger frontier input delta: {saved} tokens  ({pct:.0f}%)")
    print("direct models:", json.dumps(d["models"], indent=2))
    print("shunt  models:", json.dumps(s["models"], indent=2))
    print("direct text:", d["text"].replace("\n", " | ")[:180])
    print("shunt  text:", s["text"].replace("\n", " | ")[:180])
    if d_pay.get("file_in_parent") and not s_pay.get("file_in_parent") and s_pay.get("hook_denied"):
        print("\nPASS: shunt kept the file body out of the parent.")
    elif s_pay.get("file_in_parent"):
        print("\nFAIL: file body still landed in the parent.")
    else:
        print("\nINCONCLUSIVE: check hook_denied / file_in_parent above.")


if __name__ == "__main__":
    main()
