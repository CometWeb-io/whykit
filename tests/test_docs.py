"""Documentation must agree with the CLI it describes.

A quickstart that fails on its second command loses the reader for good, and a
flag that only exists in the docs is a bug report waiting to happen. These tests
parse every documented `whykit` command against the real argument parser, run
the README quickstart end to end, and check that relative links resolve.
"""
from __future__ import annotations

import contextlib
import io
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))

from whykit.cli import build_parser  # noqa: E402

DOC_FILES = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "apps" / "explorer" / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "examples").glob("*/README.md")),
)
SHELL_FENCE = re.compile(r"```(?:bash|sh|shell)\n(.*?)```", re.S)
WHYKIT_CALL = re.compile(r"(?:^|&&\s*|\buv run (?:--project \S+ )?)whykit (.*)")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _documented_commands(text: str) -> list[str]:
    commands: list[str] = []
    for block in SHELL_FENCE.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = WHYKIT_CALL.search(line)
            if match is None:
                continue
            command = re.split(r"\s+(?:&&|\||>|#)\s*", match.group(1))[0]
            commands.append(command)
    return commands


def _github_anchor(heading: str) -> str:
    slug = re.sub(r"[^\w\- ]", "", heading.strip().lower().replace("`", ""))
    return slug.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    return {_github_anchor(m.group(1)) for m in re.finditer(r"(?m)^#{1,6}\s+(.+)$", text)}


class DocumentedCommandTests(unittest.TestCase):
    def test_docs_contain_commands_to_check(self) -> None:
        total = sum(len(_documented_commands(p.read_text(encoding="utf-8"))) for p in DOC_FILES)
        self.assertGreater(total, 50, "the extractor stopped finding documented commands")

    def test_every_documented_command_parses(self) -> None:
        for path in DOC_FILES:
            for command in _documented_commands(path.read_text(encoding="utf-8")):
                # Placeholders such as <reviewed-sha> stand for one argument.
                argv = [re.sub(r"<[^>]+>", "X", arg) for arg in shlex.split(command)]
                with self.subTest(doc=path.relative_to(ROOT).as_posix(), command=command):
                    stderr = io.StringIO()
                    try:
                        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                            build_parser().parse_args(argv)
                    except SystemExit as exc:
                        self.assertEqual(exc.code, 0, stderr.getvalue())

    def test_the_extractor_catches_a_misplaced_option(self) -> None:
        # A leaf option on the parent of a nested command is a real parse
        # error, so a documented command that does it must fail this check.
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["new", "--owner", "Ops", "decision", "Title"])


class QuickstartTests(unittest.TestCase):
    def _quickstart_lines(self) -> list[str]:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"<!-- quickstart:start -->\n```bash\n(.*?)```\n<!-- quickstart:end -->", readme, re.S)
        self.assertIsNotNone(match, "README quickstart markers are missing")
        lines = [line.strip() for line in match.group(1).replace("\\\n", " ").splitlines()]
        return [line for line in lines if line and not line.startswith("#")]

    def test_readme_quickstart_runs_and_produces_a_linted_vault(self) -> None:
        lines = self._quickstart_lines()
        self.assertEqual(lines[:3], [
            "git clone https://github.com/CometWeb-io/whykit.git",
            "cd whykit",
            "uv sync --locked",
        ])
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "my-ledger"
            outputs = []
            for line in lines[3:]:
                self.assertTrue(line.startswith("uv run whykit "), line)
                argv = [str(vault) if arg == "../my-ledger" else arg
                        for arg in shlex.split(line.removeprefix("uv run whykit "))]
                result = subprocess.run([sys.executable, str(CLI), *argv], cwd=ROOT,
                                        text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, f"{line}\n{result.stdout}{result.stderr}")
                outputs.append(result.stdout)
            combined = "".join(outputs)
            self.assertIn("created E-001: 00-context/evidence-register.md", combined)
            self.assertIn("created D-001: 06-decisions/d-001-ship-sso-before-audit-logs.md", combined)
            self.assertRegex(combined, r"\d+ files — 0 error\(s\), 4 warning\(s\)")
            self.assertIn("06-decisions/d-001-ship-sso-before-audit-logs.md  (Platform)", combined)


class LinkTests(unittest.TestCase):
    def test_relative_links_and_anchors_resolve(self) -> None:
        for path in (*DOC_FILES, ROOT / "SECURITY.md"):
            text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
            for target in MD_LINK.findall(text):
                if re.match(r"[a-z]+:", target):
                    continue
                file_part, _, anchor = target.partition("#")
                resolved = (path.parent / file_part).resolve() if file_part else path
                with self.subTest(doc=path.relative_to(ROOT).as_posix(), link=target):
                    self.assertTrue(resolved.exists(), f"{target} does not exist")
                    if anchor and resolved.suffix == ".md":
                        self.assertIn(anchor, _anchors(resolved))


class TemplateAgreementTests(unittest.TestCase):
    def test_configuration_doc_shows_the_generated_policy(self) -> None:
        doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
        shown = re.search(r"```toml\n(.*?)```", doc, re.S).group(1)
        template = (ROOT / "src" / "whykit" / "template" / "whykit.toml").read_text(encoding="utf-8")
        generated = "".join(line for line in template.splitlines(keepends=True) if not line.startswith("#"))
        self.assertEqual(shown.strip(), generated.strip())

    def test_vault_instructions_do_not_point_at_repository_scripts(self) -> None:
        # A vault has no scripts/ directory; telling its agents to run one fails.
        for vault in (ROOT / "src" / "whykit" / "template", ROOT / "examples" / "tiny", ROOT / "examples" / "northline"):
            for note in vault.rglob("*.md"):
                with self.subTest(note=note.relative_to(ROOT).as_posix()):
                    self.assertNotIn("scripts/whykit.py", note.read_text(encoding="utf-8"))


@unittest.skipIf(shutil.which("git") is None, "git is required")
class VaultGitignoreTests(unittest.TestCase):
    def test_whykit_machine_state_is_ignored_after_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            self.assertEqual(subprocess.run([sys.executable, str(CLI), "init", str(vault)],
                                            capture_output=True).returncode, 0)
            created = subprocess.run(
                [sys.executable, str(CLI), "new", "--root", str(vault), "decision", "Example", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertTrue((vault / ".whykit" / "mutation.lock").exists())
            subprocess.run(["git", "init", "-q", str(vault)], check=True, capture_output=True)
            tracked = subprocess.run(["git", "-C", str(vault), "status", "--porcelain", "--untracked-files=all"],
                                     check=True, capture_output=True, text=True).stdout
            self.assertNotIn(".whykit/", tracked)
            self.assertIn("06-decisions/d-001-example.md", tracked)


if __name__ == "__main__":
    unittest.main()
