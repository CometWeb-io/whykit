"""Adoption details found by QA: short notes, `.MD` files, tight context budgets, `serve`.

Each class pins one rule:

* a short note with real sentences is a note; only headings, placeholders and
  unfilled template fields are `heading-only` stubs;
* a file is Markdown when its name ends in `.md` in any letter case, for lint,
  adopt, the vault index and ID allocation alike;
* `context --max-chars` spends a small budget on the body, not on front matter;
* `serve` from a copy without the Explorer says where the Explorer actually is.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _vaults import fresh_vault  # noqa: E402

from whykit import adopt as adopt_module  # noqa: E402
from whykit import cli  # noqa: E402
from whykit.adopt import scan  # noqa: E402
from whykit.context import build_context  # noqa: E402
from whykit.lint import collect_markdown, lint  # noqa: E402
from whykit.scaffold import _existing_decision_ids, _next_id  # noqa: E402
from whykit.snapshot import build_snapshot  # noqa: E402

TODAY = dt.date(2026, 9, 17)
FRONT = (
    "---\ntitle: \"{title}\"\naliases: []\ntype: guide\nstatus: draft\nowner: \"Example owner\"\n"
    "created: 2026-09-17\nlast_updated: 2026-09-17\nsource_of_truth: false\nsensitivity: internal\n"
    "source_ids: []\ntags: []\n---\n\n"
)


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        return path


class ShortNotesAreNotStubsTests(_Tmp):
    """`heading-only` is for files with nothing to keep, not for short ones."""

    REAL_SHORT_NOTES = {
        "standup.md": "# Standup 2026-03-02\n\nShipped the export. Blocked on legal review.\n",
        "one-liner.md": "# Database\n\nWe use Postgres.\n",
        "links.md": "# Reading list\n\n- https://www.example.com/paper\n",
        "wikilinks.md": "# See\n\n- [[pricing]]\n",
        "glossary.md": "# SLA\n\n**SLA** — service level agreement.\n",
        "polish.md": "# Spotkanie\n\nUstaliliśmy termin wdrożenia.\n",
        "bullets.md": "# Risks\n\n- vendor lock-in\n- cost\n",
        "table.md": "# Owners\n\n| Area | Owner |\n|---|---|\n| Billing | Ana |\n",
        "dated-field.md": "# Retro\n\nDate: 2026-03-02\n",
    }
    STUBS = {
        "todo.md": "# Title\n\nTODO\n",
        "todo-colon.md": "# Pricing\n\nTODO: write this up\n",
        "tbd.md": "# Draft\n\nTBD.\n",
        "empty-bullets.md": "# Ideas\n\n- \n- \n* [ ] \n",
        "template.md": "# Meeting\n\nDate:\nAttendees:\n\n## Notes\n\n<!-- write here -->\n",
        "lorem.md": "# Placeholder\n\nLorem ipsum dolor sit amet, consectetur adipiscing elit.\n",
        "ellipsis.md": "# WIP\n\n...\n",
        "coming-soon.md": "# Roadmap\n\n*Coming soon.*\n",
        "headings.md": "# A\n\n## B\n\n### C\n",
        "empty-table.md": "# Owners\n\n| Area | Owner |\n|---|---|\n|  |  |\n",
        "one-word.md": "# Glossary\n\nIdempotent\n",
    }

    def test_realistic_short_notes_are_useful(self) -> None:
        for name, text in self.REAL_SHORT_NOTES.items():
            self.write(self.base / name, text)
        by_name = {c.relative: c.assessment for c in scan(self.base)}
        self.assertEqual(by_name, {name: "useful" for name in self.REAL_SHORT_NOTES})

    def test_placeholders_and_blank_templates_are_still_stubs(self) -> None:
        for name, text in self.STUBS.items():
            self.write(self.base / name, text)
        by_name = {c.relative: c.assessment for c in scan(self.base)}
        self.assertEqual(by_name, {name: "heading-only" for name in self.STUBS})

    def test_long_placeholder_lines_are_classified_in_bounded_time(self) -> None:
        # The classifier must not backtrack on a pathological line.
        self.write(self.base / "dots.md", "# x\n\n" + "." * 3000 + "x\n")
        self.write(self.base / "comments.md", "# x\n\n" + "<!--" * 900)
        started = time.perf_counter()
        by_name = {c.relative: c.assessment for c in scan(self.base)}
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(set(by_name), {"dots.md", "comments.md"})
        # Kilobytes of text are never a stub, whatever they contain.
        self.write(self.base / "long.md", "# x\n\n" + "TODO\n" * 2000)
        self.assertEqual({c.relative: c.assessment for c in scan(self.base)}["long.md"], "useful")


class MarkdownExtensionCaseTests(_Tmp):
    """`.MD`, `.Md` and `.md` are all Markdown, everywhere."""

    def setUp(self) -> None:
        super().setUp()
        self.vault = self.base / "vault"
        fresh_vault(self.vault)

    def test_adopt_inventories_upper_case_extensions(self) -> None:
        source = self.base / "old-docs"
        self.write(source / "docs" / "old.MD", "# Old\n\nNotes written on a system that capitalised extensions.\n")
        self.write(source / "Mixed.Md", "# Mixed\n\nAnother note with a mixed case extension.\n")
        self.write(source / "notes.txt", "not markdown")
        self.assertEqual(sorted(c.relative for c in scan(source)), ["Mixed.Md", "docs/old.MD"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = adopt_module.main([str(source), "--into", str(self.vault), "--json"])
        self.assertEqual(code, 0)
        note = json.loads(out.getvalue())["scope_note"]
        self.assertIn("1 other file(s) excluded (e.g. notes.txt)", note)
        self.assertNotIn("old.MD", note)

    def test_lint_and_the_index_see_upper_case_notes(self) -> None:
        upper = self.write(self.vault / "notes" / "old.MD", "# Old note without front matter\n")
        files = collect_markdown(self.vault, [])
        self.assertIn(upper, files)
        self.assertEqual(collect_markdown(self.vault, ["notes"]), [p for p in files if p.parent == upper.parent])
        _, findings = lint(self.vault, today=TODAY)
        self.assertIn(("notes/old.MD", "frontmatter.missing"), {(f.path, f.code) for f in findings})

    def test_links_and_node_ids_drop_the_extension_in_any_case(self) -> None:
        self.write(self.vault / "notes" / "old.MD", FRONT.format(title="Old") + "# Old\n\nBody.\n")
        self.write(
            self.vault / "notes" / "map.md",
            FRONT.format(title="Map") + "# Map\n\n- [[old]]\n- [[notes/old]]\n- [[notes/old.MD]]\n",
        )
        _, findings = lint(self.vault, today=TODAY)
        self.assertFalse([f for f in findings if f.path == "notes/map.md" and f.code.startswith("link.")], findings)
        from whykit.graph import build_graph

        nodes = {node["id"] for node in build_graph(self.vault)["nodes"]}
        self.assertIn("notes/old", nodes)
        self.assertNotIn("notes/old.MD", nodes)

    def test_snapshot_reads_metadata_of_upper_case_notes(self) -> None:
        self.write(self.vault / "notes" / "old.MD", FRONT.format(title="Old") + "# Old\n")
        files = {entry["path"]: entry for entry in build_snapshot(self.vault, today=TODAY)["files"]}
        self.assertEqual(files["notes/old.MD"].get("title"), "Old")

    def test_decision_ids_are_never_reused_for_upper_case_records(self) -> None:
        self.write(self.vault / "06-decisions" / "d-005-legacy.MD", "# legacy\n")
        self.assertEqual(_next_id(_existing_decision_ids(self.vault), "D"), "D-006")


class ContextBudgetTests(_Tmp):
    """A small `--max-chars` should buy the note's words, not its YAML."""

    def setUp(self) -> None:
        super().setUp()
        self.vault = self.base / "vault"
        fresh_vault(self.vault)
        tags = ", ".join(f'"tag-{n}"' for n in range(30))
        self.text = (
            FRONT.format(title="Pricing notes").replace("tags: []", f"tags: [{tags}]")
            + "# Pricing notes\n\nUsage-based pricing lowers the entry price for small teams.\n\n"
            + "More detail. " * 100
        )
        self.write(self.vault / "notes" / "pricing-notes.md", self.text)

    def test_small_budget_starts_with_the_body(self) -> None:
        report = build_context(self.vault, "notes/pricing-notes", max_chars=200)
        self.assertTrue(report["content_truncated"])
        self.assertTrue(report["front_matter_omitted"])
        self.assertLessEqual(len(report["content"]), 200)
        self.assertTrue(report["content"].startswith("# Pricing notes"), report["content"])
        self.assertIn("Usage-based pricing lowers the entry price", report["content"])
        self.assertNotIn("tags:", report["content"])
        self.assertEqual(report["record"]["title"], "Pricing notes")

    def test_summary_front_matter_comes_first(self) -> None:
        path = self.vault / "notes" / "pricing-notes.md"
        self.write(path, self.text.replace("tags: [", 'summary: "Charge per seat, not per project."\ntags: [', 1))
        report = build_context(self.vault, "notes/pricing-notes", max_chars=120)
        self.assertTrue(report["content"].startswith("Charge per seat, not per project.\n\n# Pricing notes"))

    def test_a_note_that_fits_is_returned_whole(self) -> None:
        report = build_context(self.vault, "notes/pricing-notes", max_chars=100_000)
        self.assertEqual(report["content"], self.text)
        self.assertFalse(report["content_truncated"])
        self.assertNotIn("front_matter_omitted", report)

    def test_cli_json_carries_the_flag(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["context", "notes/pricing-notes", "--root", str(self.vault), "--max-chars", "200", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["front_matter_omitted"])
        self.assertFalse(payload["content"].startswith("---"))


class ServeHintTests(_Tmp):
    """`serve` without the Explorer next to it names the right next step."""

    def serve(self, origin: dict | None) -> tuple[int, str]:
        err = io.StringIO()
        packaged = self.base / "site-packages"
        packaged.mkdir(exist_ok=True)
        with mock.patch.object(cli, "SOURCE_ROOT", packaged), \
                mock.patch.object(cli, "_install_origin", return_value=origin), \
                contextlib.redirect_stderr(err):
            code = cli.main(["serve", str(self.base / "vault")])
        return code, err.getvalue()

    def test_installed_from_a_checkout_points_back_to_it(self) -> None:
        checkout = self.base / "whykit-checkout"
        self.write(checkout / "apps" / "explorer" / "package.json", "{}")
        code, err = self.serve({"url": checkout.as_uri(), "dir_info": {}})
        self.assertEqual(code, 2)
        self.assertIn(f"installed from the checkout at {checkout}", err)
        self.assertIn("uv run whykit serve", err)
        self.assertNotIn("git clone", err)

    def test_a_checkout_that_moved_falls_back_to_cloning(self) -> None:
        code, err = self.serve({"url": (self.base / "gone").as_uri(), "dir_info": {}})
        self.assertEqual(code, 2)
        self.assertIn("no longer has apps/explorer/", err)
        self.assertIn("git clone https://github.com/CometWeb-io/whykit", err)

    def test_installed_from_git_clones_that_commit(self) -> None:
        origin = {
            "url": "https://git.example.com/team/whykit.git",
            "vcs_info": {"vcs": "git", "commit_id": "0123456789abcdef0123456789abcdef01234567"},
        }
        code, err = self.serve(origin)
        self.assertEqual(code, 2)
        self.assertIn("git clone https://git.example.com/team/whykit.git whykit", err)
        self.assertIn("checkout 0123456789abcdef0123456789abcdef01234567", err)

    def test_installed_from_an_index_clones_the_repository(self) -> None:
        code, err = self.serve(None)
        self.assertEqual(code, 2)
        self.assertIn("git clone https://github.com/CometWeb-io/whykit", err)
        self.assertIn(str(self.base / "vault"), err)


if __name__ == "__main__":
    unittest.main()
