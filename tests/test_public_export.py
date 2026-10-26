"""Public exports must exclude private records and indirect disclosure paths."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whykit.cli import main  # noqa: E402
from whykit.explorer_index import _marker_pattern, build_explorer_index  # noqa: E402
from whykit.vault_index import VaultIndex  # noqa: E402


class PublicExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "private-directory-marker"
        shutil.copytree(ROOT / "examples/northline", self.vault)

    def note(self, stem: str, sensitivity: str, body: str, title: str | None = None) -> None:
        path = self.vault / "07-research" / (stem + ".md")
        path.write_text(
            f"---\ntitle: {title or stem}\ntype: research\nstatus: draft\nowner: Research\n"
            "created: 2026-09-01\nlast_updated: 2026-09-01\n"
            f"sensitivity: {sensitivity}\nsource_of_truth: false\nsource_ids: []\n---\n\n# {title or stem}\n\n{body}\n",
            encoding="utf-8",
        )

    def test_marker_matching_handles_prefixes_unicode_and_deep_families(self) -> None:
        markers = {"D-001", "D-0010", "path/żółć", "path/żółć.md", "Private (title)"}
        markers.update("x" * n for n in range(1, 501))
        pattern = _marker_pattern(markers)
        self.assertIsNotNone(pattern)
        assert pattern is not None
        for marker in markers:
            self.assertEqual(pattern.findall("[" + marker + "]"), [marker])
        self.assertEqual(pattern.findall("ZD-001 invalidD-001suffix path/żółćness"), [])

    def test_public_name_never_falls_back_to_the_private_directory(self) -> None:
        from _vaults import fresh_vault

        root = Path(self.tmp.name) / "PRIVATE-DIRECTORY-CANARY"
        fresh_vault(root, "--minimal")
        (root / "README.md").write_text("---\nsensitivity: public\n---\nPublic text.\n", encoding="utf-8")
        self.assertNotIn("PRIVATE-DIRECTORY-CANARY", json.dumps(build_explorer_index(root)))

    def test_a_longer_public_title_cannot_mask_a_private_title_reference(self) -> None:
        self.note("private-prefix", "restricted", "SECRET-CANARY", "Private prefix canary")
        self.note("long-title", "public", "Ordinary text.", "Private prefix canary extended")
        raw = json.dumps(build_explorer_index(self.vault))
        self.assertNotIn("Private prefix canary", raw)

    def test_private_note_and_its_transitive_references_are_withheld(self) -> None:
        self.note("hidden-identifier", "restricted", "PRIVATE-BODY-MARKER", "Private title marker")
        self.note("direct-reference", "public", "[[07-research/hidden-identifier|Readable private label]]")
        self.note("indirect-reference", "public", "[[07-research/direct-reference]]")
        self.note("code-reference", "public", "```\n[[07-research/hidden-identifier]]\n```")
        self.note("plain-reference", "public", "Private title marker")
        self.note("safe-public-note", "public", "PUBLIC-BODY-MARKER")
        payload = build_explorer_index(self.vault)
        raw = json.dumps(payload)
        for marker in ("hidden-identifier", "Private title marker", "PRIVATE-BODY-MARKER",
                       "direct-reference", "indirect-reference", "code-reference", "plain-reference",
                       "Readable private label", "private-directory-marker"):
            self.assertNotIn(marker, raw)
        self.assertIn("PUBLIC-BODY-MARKER", raw)
        self.assertEqual(payload["exportMode"], "public")
        self.assertTrue(all(doc["sensitivity"] == "public" for doc in payload["docs"]))
        self.assertEqual(payload["lint"]["files"], len(payload["docs"]))
        private = json.dumps(build_explorer_index(self.vault, private=True))
        self.assertIn("PRIVATE-BODY-MARKER", private)

    def test_markdown_links_and_unclassified_attachments_cannot_leak(self) -> None:
        self.note("hidden-url-marker", "confidential", "CONFIDENTIAL-MARKER")
        self.note("markdown-reference", "public", "[read](hidden-url-marker.md)")
        self.note("encoded-reference", "public", "[read](hidden%2Durl%2Dmarker.md)")
        self.note("attachment-reference", "public", "![image](private-image-marker.png)")
        payload = json.dumps(build_explorer_index(self.vault))
        for marker in ("hidden-url-marker", "CONFIDENTIAL-MARKER", "markdown-reference",
                       "encoded-reference", "attachment-reference", "private-image-marker"):
            self.assertNotIn(marker, payload)

    def test_titles_and_aliases_of_transitively_withheld_notes_are_private(self) -> None:
        self.note("hidden", "restricted", "SECRET-CANARY")
        self.note("dependent", "public", "[[07-research/hidden]]", "Dependent title canary")
        path = self.vault / "07-research/dependent.md"
        path.write_text(path.read_text(encoding="utf-8").replace("source_ids: []", "aliases: [Dependent alias canary]\nsource_ids: []"), encoding="utf-8")
        self.note("title-reference", "public", "Dependent title canary")
        self.note("alias-reference", "public", "Dependent alias canary")
        self.note("safe", "public", "SAFE-CANARY")
        raw = json.dumps(build_explorer_index(self.vault))
        for marker in ("SECRET-CANARY", "Dependent title canary", "Dependent alias canary"):
            self.assertNotIn(marker, raw)
        self.assertIn("SAFE-CANARY", raw)

    def test_unknown_missing_and_malformed_labels_fail_closed(self) -> None:
        for stem, label in (("unknown-label-marker", "secret"), ("blank-label-marker", ""),
                            ("null-label-marker", "null")):
            self.note(stem, label, "NONPUBLIC-MARKER")
        self.note("broken-front-marker", "[broken", "BROKEN-MARKER")
        self.note("ambiguous-label-marker", "public", "CASE-KEY-MARKER")
        path = self.vault / "07-research/ambiguous-label-marker.md"
        path.write_text(path.read_text(encoding="utf-8").replace("source_ids: []", "Sensitivity: restricted\nsource_ids: []"), encoding="utf-8")
        raw = json.dumps(build_explorer_index(self.vault))
        for marker in ("NONPUBLIC-MARKER", "BROKEN-MARKER", "CASE-KEY-MARKER"):
            self.assertNotIn(marker, raw)

    def test_ledgers_evidence_and_findings_inherit_their_note_visibility(self) -> None:
        for relative in ("00-context/evidence-register.md", "00-context/review-log.md",
                         "06-decisions/decision-log.md"):
            path = self.vault / relative
            path.write_text(path.read_text(encoding="utf-8").replace("sensitivity: public", "sensitivity: restricted"), encoding="utf-8")
        self.note("safe-independent-note", "public", "SAFE-MARKER")
        payload = build_explorer_index(self.vault)
        self.assertEqual(payload["evidence"], [])
        self.assertEqual(payload["reviews"], [])
        self.assertEqual(payload["decisions"], [])
        raw = json.dumps(payload)
        self.assertNotIn("E-001", raw)
        self.assertNotIn("evidence-register", raw)
        self.assertIn("SAFE-MARKER", raw)

    def test_public_cli_error_does_not_reveal_private_finding(self) -> None:
        self.note("private-error-path-marker", "restricted", "[[PRIVATE-MISSING-TARGET-MARKER]]")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["explorer-index", "--root", str(self.vault), "--today", "2026-09-17"])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["code"], "vault_invalid")
        self.assertNotIn("private-error-path-marker", out.getvalue())
        self.assertNotIn("PRIVATE-MISSING-TARGET-MARKER", out.getvalue())

    def test_cli_uses_one_snapshot_for_content_and_sensitivity(self) -> None:
        self.note("snapshot-private-marker", "restricted", "OLD-PRIVATE-CONTENT-MARKER")
        original = VaultIndex.load

        def load_and_relabel(root: Path) -> VaultIndex:
            index = original(root)
            path = root / "07-research/snapshot-private-marker.md"
            path.write_text(path.read_text(encoding="utf-8").replace("sensitivity: restricted", "sensitivity: public"), encoding="utf-8")
            return index

        out = io.StringIO()
        with mock.patch.object(VaultIndex, "load", side_effect=load_and_relabel) as load:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["explorer-index", "--root", str(self.vault), "--today", "2026-09-17"])
        self.assertEqual(code, 0, out.getvalue())
        load.assert_called_once()
        self.assertNotIn("OLD-PRIVATE-CONTENT-MARKER", out.getvalue())

    def test_ledger_rows_use_the_same_capture_as_their_labels(self) -> None:
        from _vaults import fresh_vault

        root = Path(self.tmp.name) / "ledger"
        fresh_vault(root, "--minimal")
        review = root / "00-context/review-log.md"
        review.write_text(review.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: public"), encoding="utf-8")
        original = VaultIndex.load

        def edit_after_capture(vault: Path) -> VaultIndex:
            index = original(vault)
            text = review.read_text(encoding="utf-8").replace("sensitivity: public", "sensitivity: restricted")
            review.write_text(text + "| 2026-09-17 | [[Home]] | PRIVATE-REVIEW-CANARY | confirmed | | | private |\n", encoding="utf-8")
            return index

        with mock.patch.object(VaultIndex, "load", side_effect=edit_after_capture):
            payload = build_explorer_index(root)
        self.assertNotIn("PRIVATE-REVIEW-CANARY", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
