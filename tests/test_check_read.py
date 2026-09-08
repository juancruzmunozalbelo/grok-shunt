#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "check-read.py"


def run_hook(payload: dict, env: dict | None = None) -> tuple[int, dict]:
    full_env = os.environ.copy()
    full_env.pop("SHUNT_DISABLE", None)
    full_env.pop("SHUNT_MIN_LINES", None)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=full_env,
        check=False,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def big_file(dirpath: str, lines: int) -> str:
    path = os.path.join(dirpath, "big.txt")
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(lines):
            fh.write(f"line {i}\n")
    return path


class CheckReadTests(unittest.TestCase):
    def test_targeted_read_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            code, out = run_hook(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path, "offset": 10, "limit": 20},
                }
            )
            self.assertEqual(code, 0)
            self.assertEqual(out["decision"], "allow")

    def test_untargeted_large_read_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            code, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_MIN_LINES": "350"},
            )
            self.assertEqual(code, 0)
            self.assertEqual(out["decision"], "deny")
            self.assertIn("bulk-read", out["reason"])
            self.assertIn("--paths", out["reason"])
            self.assertIn("800", out["reason"])

    def test_small_file_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 20)
            code, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}}
            )
            self.assertEqual(out["decision"], "allow")
            self.assertEqual(code, 0)

    def test_cat_large_denied_pipe_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, dump = run_hook(
                {
                    "toolName": "run_terminal_command",
                    "toolInput": {"command": f"cat {path}"},
                }
            )
            self.assertEqual(dump["decision"], "deny")
            _, piped = run_hook(
                {
                    "toolName": "run_terminal_command",
                    "toolInput": {"command": f"cat {path} | grep foo"},
                }
            )
            self.assertEqual(piped["decision"], "allow")

    def test_head_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {
                    "toolName": "run_terminal_command",
                    "toolInput": {"command": f"head {path}"},
                }
            )
            self.assertEqual(out["decision"], "allow")

    def test_subagent_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path},
                    "subagentType": "shunt:bulk-reader",
                }
            )
            self.assertEqual(out["decision"], "allow")

    def test_disable_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_DISABLE": "1"},
            )
            self.assertEqual(out["decision"], "allow")

    def test_bad_json_fail_open(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input="not-json",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(json.loads(proc.stdout)["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
