"""`whykit connect`: MCP configuration per host and the marked agent block."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _jsonschema import validate  # noqa: E402
from _vaults import fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit.connect import END_MARKER, START_MARKER, ConnectError, host_config, merge_block, ServerCommand  # noqa: E402

SCHEMA = json.loads((ROOT / "schemas" / "connect-result.schema.json").read_text(encoding="utf-8"))


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class ConnectCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.vault = self.base / "decisions"
        fresh_vault(self.vault)
        self.repo = self.base / "app"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def connect(self, *extra: str) -> tuple[int, dict, str]:
        code, out, err = call("connect", "--root", str(self.vault), "--repo", str(self.repo), "--json", *extra)
        return code, json.loads(out), err

    def test_preview_is_the_default_and_writes_nothing(self) -> None:
        code, report, _ = self.connect("--host", "cursor")
        self.assertEqual(code, 0)
        self.assertEqual(validate(report, SCHEMA), [])
        self.assertEqual(report["action"], "created")
        self.assertFalse(report["written"])
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertNotIn("_merged", report)

    def test_write_creates_then_is_idempotent(self) -> None:
        code, report, _ = self.connect("--write")
        self.assertEqual((code, report["action"], report["written"]), (0, "created", True))
        first = (self.repo / "AGENTS.md").read_bytes()
        code, report, _ = self.connect("--write")
        self.assertEqual((code, report["action"], report["written"]), (0, "unchanged", False))
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), first)

    def test_existing_instructions_are_kept_byte_for_byte(self) -> None:
        original = "# App\n\nUse tabs.\n"
        (self.repo / "AGENTS.md").write_text(original, encoding="utf-8")
        code, report, _ = self.connect("--write")
        self.assertEqual((code, report["action"]), (0, "updated"))
        text = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original + "\n" + START_MARKER))
        self.assertTrue(text.endswith(END_MARKER + "\n"))

    def test_rewrite_replaces_only_the_marked_block(self) -> None:
        stale = f"# App\n\n{START_MARKER}\nold text\n{END_MARKER}\n\n## After\nkeep me\n"
        (self.repo / "AGENTS.md").write_text(stale, encoding="utf-8")
        code, report, _ = self.connect("--write")
        self.assertEqual((code, report["action"]), (0, "updated"))
        text = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("old text", text)
        self.assertTrue(text.startswith(f"# App\n\n{START_MARKER}\n## Decisions (WhyKit)"))
        self.assertTrue(text.endswith(f"{END_MARKER}\n\n## After\nkeep me\n"))
        self.assertEqual(text.count(START_MARKER), 1)

    def test_claude_code_writes_claude_md_and_a_claude_mcp_add_command(self) -> None:
        code, report, _ = self.connect("--host", "claude-code", "--write")
        self.assertEqual(code, 0)
        self.assertEqual(Path(report["instructions_file"]).name, "CLAUDE.md")
        self.assertTrue((self.repo / "CLAUDE.md").is_file())
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertTrue(report["mcp"]["config"].startswith("claude mcp add whykit -- "))

    def test_committed_block_never_contains_an_absolute_vault_path(self) -> None:
        _, report, _ = self.connect()
        self.assertIn("vault at `../decisions`", report["block"])
        self.assertNotIn(str(self.base), report["block"])
        # The MCP configuration is user-local, so it does carry absolute paths.
        self.assertIn(str(self.vault.resolve()), report["mcp"]["args"])

    def test_vault_inside_the_repository_is_named_relatively(self) -> None:
        code, out, _ = call("connect", "--root", str(self.vault), "--repo", str(self.base), "--json")
        self.assertEqual(code, 0)
        self.assertIn("vault at `decisions`", json.loads(out)["block"])

    def test_sensitivity_ceiling_is_passed_to_the_server(self) -> None:
        _, report, _ = self.connect("--max-sensitivity", "public")
        self.assertEqual(report["mcp"]["args"][-2:], ["--max-sensitivity", "public"])

    def test_unbalanced_markers_are_refused_without_writing(self) -> None:
        broken = f"# App\n{START_MARKER}\nno end\n"
        (self.repo / "AGENTS.md").write_text(broken, encoding="utf-8")
        code, report, _ = self.connect("--write")
        self.assertEqual(code, 2)
        self.assertEqual(report["error"]["code"], "invalid_target")
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), broken)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are required")
    def test_symlinked_instructions_file_is_refused(self) -> None:
        real = self.base / "elsewhere.md"
        real.write_text("x\n", encoding="utf-8")
        try:
            (self.repo / "AGENTS.md").symlink_to(real)
        except OSError:
            self.skipTest("cannot create symlinks here")
        code, report, _ = self.connect("--write")
        self.assertEqual((code, report["error"]["code"]), (2, "unsafe_path"))
        self.assertEqual(real.read_text(encoding="utf-8"), "x\n")

    def test_missing_repository_and_vault_are_reported(self) -> None:
        code, out, _ = call("connect", "--root", str(self.vault), "--repo", str(self.base / "nope"), "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "invalid_target"))
        code, out, _ = call("connect", "--root", str(self.base / "no-vault"), "--repo", str(self.repo), "--json")
        self.assertEqual(json.loads(out)["error"]["code"], "vault_not_found")

    def test_human_output_explains_preview_and_missing_server(self) -> None:
        with mock.patch("whykit.connect.shutil.which", return_value=None):
            code, out, _ = call("connect", "--root", str(self.vault), "--repo", str(self.repo))
        self.assertEqual(code, 0)
        self.assertIn("uv tool install --upgrade 'whykit[mcp]'", out)
        self.assertIn("Run again with --write to apply.", out)


class HostConfigTests(unittest.TestCase):
    server = ServerCommand("/opt/bin/whykit-mcp", ("--root", "/v", "--max-sensitivity", "internal"), True)

    def test_json_hosts(self) -> None:
        for host in ("cursor", "claude-desktop", "generic"):
            with self.subTest(host=host):
                value = json.loads(host_config(host, self.server))
                self.assertEqual(value["mcpServers"]["whykit"]["command"], "/opt/bin/whykit-mcp")
        vscode = json.loads(host_config("vscode", self.server))
        self.assertEqual(vscode["servers"]["whykit"]["type"], "stdio")

    def test_codex_config_is_valid_toml(self) -> None:
        value = tomllib.loads(host_config("codex", self.server))
        self.assertEqual(value["mcp_servers"]["whykit"]["args"], list(self.server.args))
        self.assertTrue(value["mcp_servers"]["whykit"]["required"])


class MergeBlockTests(unittest.TestCase):
    block = f"{START_MARKER}\nbody\n{END_MARKER}"

    def test_shapes(self) -> None:
        self.assertEqual(merge_block(None, self.block), (self.block + "\n", "created"))
        self.assertEqual(merge_block("", self.block), (self.block + "\n", "updated"))
        self.assertEqual(merge_block("a", self.block)[0], "a\n\n" + self.block + "\n")
        self.assertEqual(merge_block("a\n", self.block)[0], "a\n\n" + self.block + "\n")
        self.assertEqual(merge_block("a\n\n", self.block)[0], "a\n\n" + self.block + "\n")

    def test_repeated_markers_are_refused(self) -> None:
        with self.assertRaises(ConnectError):
            merge_block(self.block + "\n" + self.block, self.block)


if __name__ == "__main__":
    unittest.main()
