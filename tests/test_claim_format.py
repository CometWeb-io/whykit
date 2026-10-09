from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from _claims import HEADER, TODAY, claim_text, claim_vault, read_view, source_snapshot
from _vaults import fresh_vault
from whykit.config import ConfigError, parse_config
from whykit.lint import lint


class ClaimFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name) / "vault").resolve()
        self.path = claim_vault(self.root)

    def view(self):
        try:
            return read_view(self.root)
        except ImportError:
            self.fail("Claims must capture the actual record and source fragments")

    def codes(self):
        return {error["code"] for error in self.view()["errors"]}

    def test_opt_in_accepts_claim_configuration(self):
        try:
            config = parse_config("[claims]\nformat_version = 1\n")
        except ConfigError:
            self.fail("The explicit claims format must be accepted")
        self.assertEqual(config["claims"], {"format_version": 1})

    def test_rejects_bad_claim_versions(self):
        for value in ("true", "2", "0", '"1"'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                parse_config(f"[claims]\nformat_version = {value}\n")

    def test_capture_uses_real_fragments_and_raw_inputs(self):
        view = self.view()
        self.assertEqual(view["errors"], [])
        relations = view["records"]["C-001"]["relations"]
        self.assertEqual(
            {row["relation"] for row in relations}, {"supports", "contradicts"}
        )
        self.assertEqual(
            relations[0]["fragment_text"], "Cached records can be read offline."
        )
        self.assertEqual(view["raw_inputs"][self.path], self.path.read_bytes())

    def test_no_opt_in_is_a_lint_error(self):
        config = self.root / "whykit.toml"
        config.write_text(
            config.read_text(encoding="utf-8").split("\n[claims]")[0], encoding="utf-8"
        )
        _, findings = lint(self.root, orphans=False, today=TODAY)
        self.assertIn(
            "claim.requires_opt_in", {f.code for f in findings if f.level == "error"}
        )

    def test_legacy_vault_unchanged(self):
        root = Path(self.tmp.name) / "legacy"
        fresh_vault(root, "--minimal")
        _, findings = lint(root, orphans=False, today=TODAY)
        self.assertFalse(any(f.code.startswith("claim.") for f in findings))

    def test_metadata_cannot_assert_verification(self):
        text = self.path.read_text(encoding="utf-8")
        for changed in (
            text.replace("statement: Cached decisions are readable offline.\n", ""),
            text.replace(
                "status: draft", "status: draft\nverification_status: supported"
            ),
            text.replace("valid_to: 2026-12-31", "valid_to: 2026-09-30"),
        ):
            with self.subTest(changed=changed[:80]):
                self.path.write_text(changed, encoding="utf-8")
                self.assertIn("claim.invalid_metadata", self.codes())
        self.path.write_text(text, encoding="utf-8")

    def test_duplicate_claim_ids_fail_closed(self):
        other = self.path.with_name("c-001-another.md")
        other.write_bytes(self.path.read_bytes())
        self.assertIn("claim.duplicate_id", self.codes())
        self.assertTrue(self.view()["records"]["C-001"]["errors"])

    def test_only_one_well_formed_evidence_table(self):
        original = self.path.read_text(encoding="utf-8")
        for extra in (
            "\n## Evidence\n\n" + HEADER,
            "\n| E-001 | supports | too | few | cells |\n",
        ):
            self.path.write_text(original + extra, encoding="utf-8")
            self.assertIn("claim.invalid_relation", self.codes())

    def test_normalization_preserves_identity(self):
        view = self.view()
        row = view["records"]["C-001"]["relations"][0]
        snapshot = self.root / row["snapshot"]
        content = snapshot.read_bytes()
        snapshot.write_bytes(b"\xef\xbb\xbf" + content.replace(b"\n", b"\r\n"))
        self.assertEqual(self.view()["errors"], [])
        snapshot.write_bytes(content.replace(b"\n", b"\r"))
        self.assertIn("claim.invalid_snapshot", self.codes())

    def test_snapshot_byte_limit(self):
        for size, bad in ((1024 * 1024, False), (1024 * 1024 + 1, True)):
            path, digest = source_snapshot(self.root, b"x" * size)
            row = f"| E-001 | supports | {path} | {digest} | lines:1-1 | 2026-10-09 | Local observation |\n"
            self.path.write_text(claim_text([row]), encoding="utf-8")
            self.assertEqual("claim.invalid_snapshot" in self.codes(), bad)

    def test_relation_count_limit(self):
        path, digest = source_snapshot(self.root, b"line\n" * 257)
        rows = [
            f"| E-001 | supports | {path} | {digest} | lines:{n}-{n} | 2026-10-09 | Observation |\n"
            for n in range(1, 258)
        ]
        for count, bad in ((256, False), (257, True)):
            self.path.write_text(claim_text(rows[:count]), encoding="utf-8")
            self.assertEqual("claim.invalid_relation" in self.codes(), bad)

    def test_unsafe_snapshot_and_bad_fragment_are_rejected(self):
        original = self.path.read_text(encoding="utf-8")
        row = original.splitlines()[-2]
        snapshot = row.split(" | ")[3]
        for fragment in ("lines:0-1", "lines:2-1", "lines:1-2"):
            self.path.write_text(
                original.replace("lines:1-1", fragment), encoding="utf-8"
            )
            self.assertIn("claim.invalid_fragment", self.codes())
        for target in (
            "../outside.txt",
            "/tmp/outside.txt",
            "00-context/claim-snapshots/CON.txt",
        ):
            self.path.write_text(original.replace(snapshot, target), encoding="utf-8")
            self.assertIn("claim.invalid_snapshot", self.codes())

    def test_symlink_and_directory_snapshot_are_rejected(self):
        row = self.view()["records"]["C-001"]["relations"][0]
        path = self.root / row["snapshot"]
        data = path.read_bytes()
        path.unlink()
        path.mkdir()
        self.assertIn("claim.invalid_snapshot", self.codes())
        path.rmdir()
        external = Path(self.tmp.name) / "external.txt"
        external.write_bytes(data)
        try:
            path.symlink_to(external)
        except OSError:
            self.skipTest("platform does not permit creating symlinks")
        self.assertIn("claim.invalid_snapshot", self.codes())

    def test_snapshot_must_be_valid_utf8(self):
        data = b"\xff"
        digest = hashlib.sha256(data).hexdigest()
        target = f"00-context/claim-snapshots/{digest}.txt"
        (self.root / target).write_bytes(data)
        self.path.write_text(
            claim_text(
                [
                    f"| E-001 | supports | {target} | {digest} | lines:1-1 | 2026-10-09 | Test |\n"
                ]
            ),
            encoding="utf-8",
        )
        self.assertIn("claim.invalid_snapshot", self.codes())
