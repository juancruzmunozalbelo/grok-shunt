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
        "SHUNT_FAKE_BULLETS",
        "GROK_HOOK_EVENT",
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


def bash(command: str, cwd: str | None = None) -> dict:
    payload: dict = {
        "toolName": "run_terminal_command",
        "toolInput": {"command": command},
    }
    if cwd:
        payload["cwd"] = cwd
    return payload


def big_file(dirpath: str, lines: int, name: str = "big.txt") -> str:
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(lines):
            fh.write(f"line {i}\n")
    return path


FAKE_BULLETS = "- f0\n- f1\n"
POST_ENV = {
    "GROK_HOOK_EVENT": "post_tool_use",
    "SHUNT_FAKE_BULLETS": FAKE_BULLETS,
}


def run_post(payload: dict, extra_env: dict | None = None, timeout: float = 3.0):
    env = dict(POST_ENV)
    if extra_env:
        env.update(extra_env)
    body = dict(payload)
    body.setdefault("hookEventName", "post_tool_use")
    body.setdefault(
        "toolResult",
        {
            "type": "Bash",
            "output_for_prompt": "FILEBODY\n" * 50,
            "output": [],
            "exit_code": 0,
        },
    )
    return run_hook(body, env=env, timeout=timeout)


def shunted_text(out: dict) -> str:
    spec = out.get("hookSpecificOutput") or {}
    updated = spec.get("updatedToolOutput")
    if isinstance(updated, dict):
        if "output_for_prompt" in updated:
            return str(updated.get("output_for_prompt") or "")
        fc = updated.get("FileContent") or {}
        return str(fc.get("content") or "")
    return str(updated or "")


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

    def test_fat_limit_pre_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path, "offset": 1, "limit": 99999},
                }
            )
            self.assertEqual(out["decision"], "allow")

    def test_untargeted_large_read_pre_allows_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, pre = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}},
                env={"SHUNT_MIN_LINES": "350"},
            )
            self.assertEqual(pre["decision"], "allow")
            _, post = run_post(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path},
                    "toolResult": {
                        "type": "ReadFile",
                        "FileContent": {
                            "content": "def f0():\n",
                            "raw_output": "def f0():\n",
                            "total_lines": 800,
                        },
                    },
                },
                extra_env={"SHUNT_MIN_LINES": "350"},
            )
            text = shunted_text(post)
            self.assertIn("omitted", text)
            self.assertIn("- f0", text)
            self.assertNotIn("def f0():", text)

    def test_small_file_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 20)
            code, out = run_hook(
                {"toolName": "read_file", "toolInput": {"target_file": path}}
            )
            self.assertEqual(out["decision"], "allow")
            self.assertEqual(code, 0)

    def test_cat_large_pre_allows_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, pre = run_hook(bash(f"cat {path}"))
            self.assertEqual(pre["decision"], "allow")
            _, post = run_post(bash(f"cat {path}"))
            text = shunted_text(post)
            self.assertIn("omitted", text)
            self.assertIn("- f0", text)
            _, piped = run_post(bash(f"cat {path} | grep foo"))
            self.assertFalse(piped.get("hookSpecificOutput"))

    def test_cat_pipe_cat_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_post(bash(f"cat {path} | cat"))
            self.assertIn("omitted", shunted_text(out))
            _, tee = run_post(bash(f"cat {path} | tee"))
            self.assertIn("omitted", shunted_text(tee))

    def test_cat_pipe_head_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_hook(bash(f"cat {path} | head"))
            self.assertEqual(out["decision"], "allow")
            _, counted = run_hook(bash(f"cat {path} | wc -l"))
            self.assertEqual(counted["decision"], "allow")
            _, tgrep = run_post(bash(f"cat {path} | tgrep -- foo"))
            self.assertFalse(tgrep.get("hookSpecificOutput"))

    def test_filter_then_cat_still_shunts_other_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            other = big_file(tmp, 800, "other.txt")
            _, out = run_post(bash(f"cat {path} | grep foo && cat {other}"))
            self.assertIn("omitted", shunted_text(out))

    def test_head_and_tail_are_not_dumps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, head = run_post(bash(f"head {path}"))
            self.assertFalse(head.get("hookSpecificOutput"))
            _, tail = run_post(bash(f"tail {path}"))
            self.assertFalse(tail.get("hookSpecificOutput"))

    def test_cat_and_true_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_post(bash(f"cat {path} && true"))
            self.assertIn("omitted", shunted_text(out))

    def test_cat_semi_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_post(bash(f"cat {path}; true"))
            self.assertIn("omitted", shunted_text(out))

    def test_cat_multi_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            big = big_file(tmp, 800, "a.txt")
            small = big_file(tmp, 10, "b.txt")
            _, out = run_post(bash(f"cat {big} {small}"))
            self.assertIn("omitted", shunted_text(out))

    def test_subagent_still_shunts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_post(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path},
                    "subagentType": "shunt:bulk-reader",
                    "toolResult": {
                        "type": "ReadFile",
                        "FileContent": {"content": "body", "total_lines": 800},
                    },
                }
            )
            self.assertIn("omitted", shunted_text(out))

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

    def test_disable_zero_still_shunts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_post(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path},
                    "toolResult": {
                        "type": "ReadFile",
                        "FileContent": {"content": "body", "total_lines": 800},
                    },
                },
                extra_env={"SHUNT_DISABLE": "0"},
            )
            self.assertIn("omitted", shunted_text(out))

    def test_oneline_blob_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "blob.txt")
            with open(path, "wb") as fh:
                fh.write(b"x" * 200_000)
            _, out = run_post(
                {
                    "toolName": "read_file",
                    "toolInput": {"target_file": path},
                    "toolResult": {
                        "type": "ReadFile",
                        "FileContent": {"content": "x" * 200, "total_lines": 1},
                    },
                }
            )
            self.assertIn("omitted", shunted_text(out))

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

    def test_grep_and_tgrep_are_searches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, grep_tool = run_post(
                {"toolName": "grep", "toolInput": {"pattern": "^def ", "path": path}}
            )
            self.assertFalse(grep_tool.get("hookSpecificOutput"))
            _, bash_grep = run_post(bash(f"grep foo {path}"))
            self.assertFalse(bash_grep.get("hookSpecificOutput"))
            _, tgrep = run_post(bash(f'tgrep -F -- "foo" {path}'))
            self.assertFalse(tgrep.get("hookSpecificOutput"))
            _, tgrep_dir = run_post(bash(f'tgrep -- "foo" {tmp}'))
            self.assertFalse(tgrep_dir.get("hookSpecificOutput"))

    def test_python_c_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800)
            _, out = run_post(
                bash(f'python3 -c "print(open({path!r}).read())"')
            )
            self.assertIn("omitted", shunted_text(out))

    def test_python_c_relative_post_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            big_file(tmp, 800, "big.py")
            _, out = run_post(
                bash("python3 -c \"print(open('big.py').read())\"", cwd=tmp)
            )
            self.assertIn("omitted", shunted_text(out))

    def test_bulk_read_in_filename_still_shunts_cat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = big_file(tmp, 800, "notes-bulk-read.md")
            _, out = run_post(bash(f"cat {path}"))
            self.assertIn("omitted", shunted_text(out))

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
            post = data["hooks"]["PostToolUse"][0]
            self.assertIn("read_file", post["matcher"])
            self.assertEqual(post["hooks"][0]["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
