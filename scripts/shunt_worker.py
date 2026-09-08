"""MiniMax chat client for shunt Layer-2 scripts.

Files go in the prompt. The model has no local tools. Stdout is the only
channel back to the frontier. Never print the API key.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_BYTES = 8_000_000
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"shunt: {msg}\n")
    raise SystemExit(code)


def load_api_key() -> str:
    for name in ("MINIMAX_API_KEY", "SHUNT_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if raw:
            return raw
    env_file = Path.home() / ".grok" / "minimax.env"
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        if line.startswith("MINIMAX_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("'\"")
            if key:
                return key
    die("no MINIMAX_API_KEY in the environment or ~/.grok/minimax.env")
    raise AssertionError


def settings() -> tuple[str, str, int, int]:
    base = os.environ.get("SHUNT_BASE_URL") or os.environ.get(
        "MINIMAX_BASE_URL", DEFAULT_BASE_URL
    )
    model = os.environ.get("SHUNT_MODEL", DEFAULT_MODEL)
    try:
        timeout = int(os.environ.get("SHUNT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    try:
        max_bytes = int(os.environ.get("SHUNT_MAX_BYTES", str(DEFAULT_MAX_BYTES)))
    except ValueError:
        max_bytes = DEFAULT_MAX_BYTES
    return base.rstrip("/"), model, max(1, timeout), max(1, max_bytes)


def pack_files(paths: list[str], cwd: str | None = None) -> str:
    root = Path(cwd or os.getcwd())
    chunks: list[str] = []
    total = 0
    _, _, _, max_bytes = settings()
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = (root / path).resolve()
        try:
            data = path.read_bytes()
        except OSError as exc:
            die(f"cannot read {path}: {exc}", 1)
        if b"\0" in data:
            die(f"{path} looks binary; skip it", 1)
        total += len(data)
        if total > max_bytes:
            die(
                f"packed files exceed {max_bytes} bytes; split the job "
                "(SHUNT_MAX_BYTES)",
                1,
            )
        text = data.decode("utf-8", errors="replace")
        chunks.append(f'<file path="{path}">\n{text}\n</file>')
    if not chunks:
        die("no paths", 1)
    return "\n\n".join(chunks)


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text).strip()


def strip_fences(text: str) -> str:
    text = strip_think(text).strip()
    if not text.startswith("```"):
        return text if text.endswith("\n") else text + "\n"
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    body = "\n".join(lines).strip()
    return body + "\n"


def _content_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(content)


def chat(messages: list[dict], *, temperature: float = 0.2) -> tuple[str, dict]:
    base, model, timeout, _ = settings()
    key = load_api_key()
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:500]
        die(f"worker HTTP {exc.code}: {err}", 1)
    except urllib.error.URLError as exc:
        die(f"worker request failed: {exc.reason}", 1)
    except TimeoutError:
        die(f"worker timed out after {timeout}s", 1)

    choices = body.get("choices") or []
    if not choices:
        die("worker returned no choices", 1)
    message = choices[0].get("message") or {}
    text = strip_think(_content_text(message.get("content")))
    usage = body.get("usage") or {}
    return text, usage


def report_usage(usage: dict) -> None:
    _, model, _, _ = settings()
    prompt = usage.get("prompt_tokens", "?")
    completion = usage.get("completion_tokens", "?")
    sys.stderr.write(
        f"shunt: {model} prompt={prompt} completion={completion}\n"
    )
