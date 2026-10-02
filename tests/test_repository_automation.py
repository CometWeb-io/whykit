"""Policy tests for the repository's own automation.

The workflows, the composite Action and the helper scripts are part of what
adopters run, so they get the same regression treatment as the linter. The
checks parse YAML line by line on purpose: the runtime has no dependencies and
the suite must not grow one just to read its own CI files.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTION = ROOT / "action.yml"
SHA_PIN = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")


def _uses(path: Path) -> list[str]:
    refs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:-\s+)?uses:\s*(.+?)\s*$", line)
        if match:
            refs.append(match.group(1))
    return refs


def _block(text: str, key: str) -> list[str]:
    """Lines of a top-level mapping such as ``inputs:``, without the key line."""
    lines = text.splitlines()
    start = lines.index(f"{key}:")
    out = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        out.append(line)
    return out


def _children(block: list[str], indent: int) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    current = None
    for line in block:
        match = re.match(rf"^ {{{indent}}}([\w-]+):\s*$", line)
        if match:
            current = match.group(1)
            children[current] = []
        elif current is not None:
            children[current].append(line)
    return children


def _action_step_script(name: str) -> str:
    """The ``run: |`` body of the named composite step, dedented."""
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"    - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i] == "      run: |")
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and not line.startswith("        "):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


class PinnedAndLeastPrivilegeTests(unittest.TestCase):
    def test_every_remote_action_is_pinned_to_a_commit_with_a_version_comment(self) -> None:
        for path in [*WORKFLOWS, ACTION]:
            for ref in _uses(path):
                if ref.startswith("./"):
                    continue
                with self.subTest(file=path.name, ref=ref):
                    self.assertRegex(ref, SHA_PIN)

    def test_workflows_declare_top_level_permissions(self) -> None:
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertRegex(text, r"(?m)^permissions:")
                self.assertNotIn("write-all", text)
                self.assertNotIn("pull_request_target", text)

    def test_checkouts_do_not_persist_the_token(self) -> None:
        for path in WORKFLOWS:
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "uses: actions/checkout@" not in line:
                    continue
                indent = len(line) - len(line.lstrip(" -"))
                step = []
                for following in lines[index + 1 :]:
                    stripped = following.lstrip(" ")
                    if following.strip() and (
                        len(following) - len(stripped) < indent or stripped.startswith("- ")
                    ):
                        break
                    step.append(following)
                with self.subTest(file=path.name, line=index + 1):
                    self.assertIn("persist-credentials: false", "\n".join(step))

    def test_release_publishing_fires_only_on_version_tags_without_caches(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        triggers = "\n".join(_block(text, "on"))
        self.assertEqual(triggers.strip(), 'push:\n    tags: ["v*"]')
        self.assertNotIn("enable-cache: true", text)
        # No other workflow may hold the OIDC permission PyPI trusts.
        for path in WORKFLOWS:
            if path.name != "release.yml":
                text = path.read_text(encoding="utf-8")
                with self.subTest(file=path.name):
                    self.assertNotRegex(text, r"(?m)^\s*id-token:\s*write")
                    self.assertNotIn("pypi-publish", text)

    def test_dependabot_covers_every_ecosystem_with_a_cooldown(self) -> None:
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        entries = text.split("- package-ecosystem: ")[1:]
        ecosystems = {entry.split()[0] for entry in entries}
        self.assertEqual(ecosystems, {"uv", "github-actions", "npm"})
        for entry in entries:
            with self.subTest(ecosystem=entry.split()[0]):
                self.assertRegex(entry, r"cooldown:\s+default-days: \d+")


class CompositeActionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = ACTION.read_text(encoding="utf-8")

    def test_inputs_and_outputs_are_documented_and_used(self) -> None:
        inputs = _children(_block(self.text, "inputs"), 2)
        outputs = _children(_block(self.text, "outputs"), 2)
        self.assertEqual(
            set(inputs), {"root", "profile", "strict", "base", "history", "today"}
        )
        self.assertEqual(set(outputs), {"version"})
        for name, body in [*inputs.items(), *outputs.items()]:
            with self.subTest(name=name):
                self.assertTrue(any(line.strip().startswith("description:") for line in body))
        for name, body in inputs.items():
            with self.subTest(input=name):
                self.assertIn("    required: false", body)
                self.assertIn("${{ inputs." + name + " }}", self.text)
        referenced = set(re.findall(r"inputs\.([\w-]+)", self.text))
        self.assertLessEqual(referenced, set(inputs))

    def test_inputs_reach_shell_only_through_the_environment(self) -> None:
        # `${{ inputs.x }}` inside a run script is template injection; values
        # must arrive as environment variables and be quoted by the shell.
        scripts = re.findall(r"run: \|\n((?:        .*\n|\n)+)", self.text)
        self.assertGreaterEqual(len(scripts), 4)
        for script in scripts:
            self.assertNotIn("${{", script)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "needs a POSIX bash")
    def test_gates_validate_inputs_and_forward_today(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "argv"
            fake = Path(tmp) / "whykit"
            fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{record}"\n', encoding="utf-8")
            fake.chmod(0o755)
            base_env = {**os.environ, "WHYKIT": str(fake), "WHYKIT_ROOT": "vault"}

            def run(step: str, **env: str) -> subprocess.CompletedProcess[str]:
                record.unlink(missing_ok=True)
                return subprocess.run(
                    ["bash", "-c", _action_step_script(step)],
                    cwd=ROOT, env={**base_env, **env}, text=True, capture_output=True, timeout=30,
                )

            lint = "Lint the vault (legacy mode)"
            ok = run(lint, WHYKIT_STRICT="true", WHYKIT_TODAY="2026-09-17")
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertEqual(
                record.read_text().split(),
                ["lint", "--root", "vault", "--strict", "--today", "2026-09-17"],
            )
            bad_strict = run(lint, WHYKIT_STRICT="yes", WHYKIT_TODAY="")
            self.assertEqual(bad_strict.returncode, 2)
            self.assertIn("strict must be", bad_strict.stdout)
            self.assertFalse(record.exists())
            bad_today = run(lint, WHYKIT_STRICT="false", WHYKIT_TODAY="2026-09-17; rm -rf /")
            self.assertEqual(bad_today.returncode, 2)
            self.assertIn("today must be", bad_today.stdout)
            self.assertFalse(record.exists())

            gate = "Repository policy gate"
            policy = run(
                gate, WHYKIT_PROFILE="ci", WHYKIT_HISTORY="false", WHYKIT_BASE="",
                WHYKIT_BASE_SHA="", WHYKIT_EVENT="push", WHYKIT_TODAY="2026-09-17",
            )
            self.assertEqual(policy.returncode, 0, policy.stdout + policy.stderr)
            self.assertEqual(
                record.read_text().split(),
                ["check", "--root", "vault", "--profile", "ci", "--today", "2026-09-17"],
            )
            bad_history = run(
                gate, WHYKIT_PROFILE="ci", WHYKIT_HISTORY="maybe", WHYKIT_BASE="",
                WHYKIT_BASE_SHA="", WHYKIT_EVENT="push", WHYKIT_TODAY="",
            )
            self.assertEqual(bad_history.returncode, 2)
            self.assertFalse(record.exists())


@unittest.skipIf(shutil.which("git") is None or os.name == "nt", "needs git and symlinks")
class InstallHooksTests(unittest.TestCase):
    def _git(self, *args: str, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=30)

    def test_installs_into_a_linked_worktree(self) -> None:
        # In a linked worktree `.git` is a file, so a hard-coded .git/hooks
        # path cannot exist. The hook must land where Git will look for it.
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main"
            (main / "scripts").mkdir(parents=True)
            for name in ("install-hooks.sh", "pre-commit"):
                shutil.copy2(ROOT / "scripts" / name, main / "scripts" / name)
            self._git("init", "-q", "-b", "main", cwd=main)
            self._git("add", ".", cwd=main)
            self._git(
                "-c", "user.name=Example", "-c", "user.email=dev@example.com",
                "commit", "-q", "-m", "init", cwd=main,
            )
            linked = Path(tmp) / "linked"
            self._git("worktree", "add", "-q", str(linked), cwd=main)
            self.assertTrue((linked / ".git").is_file())

            result = subprocess.run(
                ["sh", "scripts/install-hooks.sh"], cwd=linked, text=True,
                capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            hooks = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
                cwd=linked, text=True, capture_output=True, check=True, timeout=30,
            ).stdout.strip()
            hook = Path(hooks) / "pre-commit"
            self.assertTrue(hook.is_symlink())
            self.assertEqual(hook.resolve(), (linked / "scripts" / "pre-commit").resolve())


if __name__ == "__main__":
    unittest.main()
