"""Text I/O must name its encoding, or Windows falls back to the ANSI code page."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _implicit_encoding_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "write_text" and len(node.args) < 2:
            hits.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "read_text" and not node.args:
            hits.append(node.lineno)
    return hits


class TextEncodingTests(unittest.TestCase):
    def test_read_and_write_text_name_their_encoding(self) -> None:
        offenders = [
            f"{path.relative_to(ROOT).as_posix()}:{line}"
            for folder in ("src", "tests")
            for path in sorted((ROOT / folder).rglob("*.py"))
            for line in _implicit_encoding_calls(path)
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
