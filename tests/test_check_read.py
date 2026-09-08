#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "check-read.py"
INSTALL = ROOT / "scripts" / "install-user-hook"


def run_hook(
    payload: dict, env: dict | None = None, timeout: float = 3.0
) -> tuple[int, dict]:
    full_env = os.environ.copy()
    for k in (
        "SHUNT_DISABLE",
        "SHUNT_MIN_LINES",
        "SHUNT_MIN_BYTES",
        "SHUNT_MAX_LIMIT",
    ):
        full_env.pop(k, None)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=full_env,
        check=False,
        timeout=timeout,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def bash(command: str) -> dict:
    return {"toolName": "run_terminal_command", "toolInput": {"command": command}}


def big_file(dirpath: str, lines: int, name: str = "big.txt") -> str:
    path = os.path.join(dirpath, name)
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

    def test_fat_limit_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path, "offset": 1, "limit": 99999},
                }
            )
            self.assertEqual(out["decision"], "deny")

    def test_offset_only_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path, "offset": 10},
                }
            )
            self.assertEqual(out["decision"], "deny")

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
            _, dump = run_hook(bash(f"cat {path}"))
            self.assertEqual(dump["decision"], "deny")
            _, piped = run_hook(bash(f"cat {path} | grep foo"))
            self.assertEqual(piped["decision"], "allow")

    def test_head_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"head {path}"))
            self.assertEqual(out["decision"], "deny")

    def test_head_n_9999_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"head -n 9999 {path}"))
            self.assertEqual(out["decision"], "deny")

    def test_tail_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"tail {path}"))
            self.assertEqual(out["decision"], "deny")

    def test_cat_and_true_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"cat {path} && true"))
            self.assertEqual(out["decision"], "deny")

    def test_cat_semi_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"cat {path}; true"))
            self.assertEqual(out["decision"], "deny")

    def test_cat_multi_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            big = big_file(tmp, 800, "a.txt")
            small = big_file(tmp, 10, "b.txt")
            _, out = run_hook(bash(f"cat {big} {small}"))
            self.assertEqual(out["decision"], "deny")

    def test_subagent_does_not_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path},
                    "subagentType": "shunt:bulk-reader",
                }
            )
            self.assertEqual(out["decision"], "deny")

    def test_disable_true_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_DISABLE": "1"},
            )
            self.assertEqual(out["decision"], "allow")
            _, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_DISABLE": "true"},
            )
            self.assertEqual(out["decision"], "allow")

    def test_disable_zero_still_denies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_DISABLE": "0"},
            )
            self.assertEqual(out["decision"], "deny")
            _, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_DISABLE": "false"},
            )
            self.assertEqual(out["decision"], "deny")

    def test_oneline_blob_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "blob.txt")
            with open(path, "wb") as fh:
                fh.write(b"x" * 200_000)
            _, out = run_hook({"toolName": "read_file", "toolInput": {"target_file": path}})
            self.assertEqual(out["decision"], "deny")

    def test_fifo_does_not_hang(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pipe")
            os.mkfifo(path)
            t0 = time.monotonic()
            _, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                timeout=2.0,
            )
            elapsed = time.monotonic() - t0
            self.assertEqual(out["decision"], "allow")
            self.assertLess(elapsed, 1.5)

    def test_grep_tool_large_file_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {"toolName": "grep", "toolInput": {"pattern": "^def ", "path": path}}
            )
            self.assertEqual(out["decision"], "deny")

    def test_grep_tool_dir_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            big_file(tmp, 800)
            _, out = run_hook(
                {"toolName": "grep", "toolInput": {"pattern": "foo", "path": tmp}}
            )
            self.assertEqual(out["decision"], "allow")

    def test_bash_grep_file_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"grep foo {path}"))
            self.assertEqual(out["decision"], "deny")

    def test_python_c_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                bash(f'python3 -c "print(open({path!r}).read())"')
            )
            self.assertEqual(out["decision"], "deny")

    def test_python_bulk_read_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            script = Path(__file__).resolve().parents[1] / "scripts" / "bulk-read"
            _, out = run_hook(
                bash(f"python3 {script} --question q --paths {path}")
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


class InstallUserHookTests(unittest.TestCase):
    def test_writes_matcher_with_grep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["GROK_PLUGIN_ROOT"] = str(ROOT)
            proc = subprocess.run(
                [sys.executable, str(INSTALL)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            dest = home / ".grok" / "hooks" / "shunt.json"
            data = json.loads(dest.read_text(encoding="utf-8"))
            matcher = data["hooks"]["PreToolUse"][0]["matcher"]
            self.assertIn("grep", matcher)
            cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertTrue(cmd.endswith("check-read.py"))


if __name__ == "__main__":
    unittest.main()
