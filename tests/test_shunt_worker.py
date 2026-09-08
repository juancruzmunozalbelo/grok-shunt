#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import shunt_worker  # noqa: E402


def load_cli(name: str, path: Path):
    return SourceFileLoader(name, str(path)).load_module()


bulk_read = load_cli("bulk_read_cli", SCRIPTS / "bulk-read")
code_write = load_cli("code_write_cli", SCRIPTS / "code-write")


class PackAndStripTests(unittest.TestCase):
    def test_pack_files_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.py"
            path.write_text("def f0():\n    return 0\n", encoding="utf-8")
            packed = shunt_worker.pack_files([str(path)])
            self.assertIn('<file path="', packed)
            self.assertIn("def f0():", packed)
            self.assertIn("</file>", packed)

    def test_pack_missing_dies(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            shunt_worker.pack_files(["/no/such/file.py"])
        self.assertEqual(ctx.exception.code, 1)

    def test_strip_think(self) -> None:
        raw = "<think>secret corpus</think>\n- f0\n"
        self.assertEqual(shunt_worker.strip_think(raw), "- f0")

    def test_strip_fences(self) -> None:
        raw = "```python\nhello()\n```"
        self.assertEqual(shunt_worker.strip_fences(raw), "hello()\n")


class KeyAndCliTests(unittest.TestCase):
    def test_load_api_key_from_env(self) -> None:
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "tok_test"}, clear=False):
            self.assertEqual(shunt_worker.load_api_key(), "tok_test")

    def test_bulk_read_stdout_is_worker_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.py"
            path.write_text("def f50():\n    return 50\n" * 10, encoding="utf-8")
            fake = ("- f0\n- f1\n- f2", {"prompt_tokens": 9, "completion_tokens": 3})
            with patch.object(bulk_read, "chat", return_value=fake):
                with patch.object(bulk_read, "report_usage"):
                    with patch("sys.stdout") as stdout:
                        code = bulk_read.main(
                            ["--question", "first three names", "--paths", str(path)]
                        )
            self.assertEqual(code, 0)
            written = "".join(
                call.args[0] for call in stdout.write.call_args_list if call.args
            )
            self.assertIn("- f0", written)
            self.assertNotIn("def f50", written)

    def test_code_write_requires_reference_and_writes_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.py"
            ref.write_text("def sample():\n    return 1\n", encoding="utf-8")
            target = Path(tmp) / "out.py"
            fake = (
                "```python\ndef generated():\n    return 2\n```",
                {"prompt_tokens": 4, "completion_tokens": 8},
            )
            with patch.object(code_write, "chat", return_value=fake):
                with patch.object(code_write, "report_usage"):
                    with patch("sys.stdout") as stdout:
                        code = code_write.main(
                            [
                                "--spec",
                                "mirror sample",
                                "--reference",
                                str(ref),
                                "--target",
                                str(target),
                            ]
                        )
            self.assertEqual(code, 0)
            self.assertTrue(target.is_file())
            self.assertIn("def generated", target.read_text(encoding="utf-8"))
            self.assertNotIn("```", target.read_text(encoding="utf-8"))
            written = "".join(
                call.args[0] for call in stdout.write.call_args_list if call.args
            )
            self.assertTrue(written.startswith("wrote "))
            self.assertNotIn("def generated", written)

    def test_code_write_missing_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                code_write.main(
                    [
                        "--spec",
                        "x",
                        "--reference",
                        str(Path(tmp) / "missing.py"),
                        "--target",
                        str(Path(tmp) / "out.py"),
                    ]
                )
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
