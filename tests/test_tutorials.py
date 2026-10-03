"""Run every tutorial in ``docs/tutorials/`` exactly as a reader would.

A tutorial is a script with prose around it. Each ``bash`` block runs in a
real shell, in order, in one scratch directory, with a ``whykit`` on ``PATH``
that runs this checkout. The ``text`` block that follows a ``bash`` block is
the output the reader is promised: its lines must appear in the block's output,
in order. ``<...>`` in an expected line matches anything (a path, today's
date). Two HTML comments, invisible on GitHub, steer the runner:

* ``<!-- tutorial: exit=N -->`` before a ``bash`` block: the block ends with
  exit status N instead of 0;
* ``<!-- tutorial: file=PATH -->`` before any block: write the block's content
  to PATH, relative to the shell's current directory, as the reader is told to.

Blocks run with ``set -e``, so a command that fails early is caught even when
the block is expected to fail at its last command.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TUTORIALS = sorted((ROOT / "docs" / "tutorials").glob("*.md"))
FENCE = re.compile(r"(?:<!-- tutorial: (?P<directive>[^>]*?) -->\n)?```(?P<lang>[\w-]*)\n(?P<body>.*?)```", re.S)
BASH = shutil.which("bash")
GIT = shutil.which("git")


@dataclass
class Step:
    kind: str  # "run" or "file"
    body: str
    exit_code: int = 0
    path: str = ""
    expected: list[str] = field(default_factory=list)


def parse_tutorial(text: str) -> list[Step]:
    steps: list[Step] = []
    for match in FENCE.finditer(text):
        directive = dict(
            item.split("=", 1) for item in (match.group("directive") or "").split() if "=" in item
        )
        lang, body = match.group("lang"), match.group("body")
        if "file" in directive:
            steps.append(Step("file", body, path=directive["file"]))
        elif lang == "bash":
            steps.append(Step("run", body, exit_code=int(directive.get("exit", "0"))))
        elif lang == "text" and steps and steps[-1].kind == "run" and not steps[-1].expected:
            steps[-1].expected = [line for line in body.splitlines() if line.strip()]
    return steps


def _line_pattern(expected: str) -> re.Pattern[str]:
    parts = re.split(r"<[^>]+>", expected.rstrip())
    return re.compile(".+?".join(re.escape(part) for part in parts) + r"\s*$")


def missing_lines(expected: list[str], output: str) -> list[str]:
    """Expected lines not found, in order, in ``output``."""
    lines = [line.rstrip() for line in output.splitlines()]
    position = 0
    missing: list[str] = []
    for wanted in expected:
        pattern = _line_pattern(wanted)
        for index in range(position, len(lines)):
            if pattern.match(lines[index]):
                position = index + 1
                break
        else:
            missing.append(wanted)
    return missing


class TutorialFormatTests(unittest.TestCase):
    def test_there_are_tutorials_and_each_promises_output(self) -> None:
        self.assertGreaterEqual(len(TUTORIALS), 2)
        for path in TUTORIALS:
            with self.subTest(tutorial=path.name):
                steps = parse_tutorial(path.read_text(encoding="utf-8"))
                runs = [step for step in steps if step.kind == "run"]
                self.assertGreaterEqual(len(runs), 4)
                self.assertGreaterEqual(sum(bool(step.expected) for step in runs), 4)
                self.assertTrue(any(step.exit_code == 1 for step in runs), "show a failing gate, too")

    def test_matcher_respects_order_and_placeholders(self) -> None:
        output = "WhyKit vault created: /tmp/x\ncreated E-001: a.md\n"
        self.assertEqual(missing_lines(["WhyKit vault created: <path>", "created E-001: a.md"], output), [])
        self.assertEqual(missing_lines(["created E-001: a.md", "WhyKit vault created: <path>"], output),
                         ["WhyKit vault created: <path>"])
        self.assertEqual(missing_lines(["created E-002: a.md"], output), ["created E-002: a.md"])

    def test_the_tutorial_workflow_is_the_documented_ci_workflow(self) -> None:
        # One workflow, shown twice; a fix to one must reach the other.
        tutorial = (ROOT / "docs" / "tutorials" / "gate-pull-requests.md").read_text(encoding="utf-8")
        workflow = next(s.body for s in parse_tutorial(tutorial) if s.path == ".github/workflows/whykit.yml")
        self.assertIn(f"```yaml\n{workflow}```", (ROOT / "docs" / "ci.md").read_text(encoding="utf-8"))
        inputs = set(re.findall(r"(?m)^  ([a-z_]+):\n    description:", (ROOT / "action.yml").read_text(encoding="utf-8")))
        used = re.search(r"uses: CometWeb-io/whykit@\S+\n\s+with:\n((?:\s+\w+: .+\n)+)", workflow)
        self.assertIsNotNone(used)
        for key in re.findall(r"(\w+):", used.group(1)):
            self.assertIn(key, inputs)
        self.assertIn("fetch-depth: 0", workflow)


@unittest.skipIf(BASH is None or GIT is None or os.name == "nt", "the tutorials are POSIX shell sessions")
class TutorialRunTests(unittest.TestCase):
    def run_tutorial(self, path: Path) -> None:
        steps = parse_tutorial(path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bin_dir, home, work = base / "bin", base / "home", base / "work"
            for directory in (bin_dir, home, work):
                directory.mkdir()
            shim = bin_dir / "whykit"
            shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "scripts" / "whykit.py"}" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "HOME": str(home),
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "Example Author", "GIT_AUTHOR_EMAIL": "author@example.com",
                "GIT_COMMITTER_NAME": "Example Author", "GIT_COMMITTER_EMAIL": "author@example.com",
            }
            cwd_file = base / "cwd"
            cwd_file.write_text(str(work), encoding="utf-8")
            for number, step in enumerate(steps, 1):
                cwd = Path(cwd_file.read_text(encoding="utf-8").strip())
                if step.kind == "file":
                    target = cwd / step.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(step.body, encoding="utf-8")
                    continue
                script = f'set -e\ntrap \'pwd > "{cwd_file}"\' EXIT\n{step.body}'
                result = subprocess.run(
                    [BASH, "--noprofile", "--norc", "-c", script], cwd=cwd, env=env,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                )
                output = result.stdout + result.stderr
                context = f"{path.name} block {number}:\n{step.body}\n--- output ---\n{output}"
                self.assertEqual(result.returncode, step.exit_code, context)
                self.assertEqual(missing_lines(step.expected, output), [], context)

    def test_tutorials_run_as_written(self) -> None:
        for path in TUTORIALS:
            with self.subTest(tutorial=path.name):
                self.run_tutorial(path)


if __name__ == "__main__":
    unittest.main()
