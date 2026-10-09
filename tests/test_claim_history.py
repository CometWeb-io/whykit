from __future__ import annotations
import datetime as dt
import subprocess
from test_claim_approval import ClaimApprovalTests
from _claims import TODAY
from whykit import immutability
from whykit.scaffold import _frontmatter_replace


class ClaimHistoryTests(ClaimApprovalTests):
    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True, encoding="utf-8"
        ).strip()

    def commit(self):
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=MaciejZet",
            "-c",
            "user.email=maciekzmitruk@protonmail.com",
            "commit",
            "-qm",
            "fixture",
        )
        return self.git("rev-parse", "HEAD")

    def baseline(self):
        self.git("init", "-q")
        self.accept()
        return self.commit()

    def findings(self, base):
        method = getattr(immutability, "claim_history_findings", None)
        self.assertTrue(
            callable(method), "History must cover approved claims and local fragments"
        )
        return method(base, "HEAD", root=str(self.root))

    def test_semantics_and_snapshot_changes_are_immutable(self):
        for kind in (
            "statement",
            "owner",
            "sensitivity",
            "relation",
            "snapshot",
            "delete",
        ):
            with self.subTest(kind=kind):
                if (self.root / ".git").exists():
                    self.git("reset", "--hard", "HEAD")
                base = (
                    self.baseline()
                    if not (self.root / ".git").exists()
                    else self.git("rev-parse", "HEAD")
                )
                if kind in {"snapshot", "delete"}:
                    p = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
                    if kind == "snapshot":
                        p.write_bytes(p.read_bytes() + b"changed\n")
                    else:
                        p.unlink()
                else:
                    text = self.path.read_text(encoding="utf-8")
                    text = (
                        text.replace("supports", "contradicts", 1)
                        if kind == "relation"
                        else _frontmatter_replace(text, kind, "Changed")
                    )
                    self.path.write_text(text, encoding="utf-8")
                self.commit()
                self.assertTrue(self.findings(base))
                self.git("reset", "--hard", base)

    def test_new_approval_without_event_and_replayed_reset_fail(self):
        self.git("init", "-q")
        base = self.commit()
        text = self.path.read_text(encoding="utf-8")
        for key, value in (
            ("status", "approved"),
            ("last_verified", TODAY.isoformat()),
            ("review_by", "2027-01-01"),
        ):
            text = _frontmatter_replace(text, key, value)
        self.path.write_text(text, encoding="utf-8")
        self.commit()
        self.assertTrue(self.findings(base))
        self.git("reset", "--hard", base)
        base = self.baseline()
        self.path.write_text(
            _frontmatter_replace(
                self.path.read_text(encoding="utf-8"), "status", "draft"
            ),
            encoding="utf-8",
        )
        self.commit()
        self.assertTrue(self.findings(base))

    def test_confirmed_is_allowed_but_date_edit_without_event_is_not(self):
        from whykit.review import record_review

        base = self.baseline()
        kw = dict(
            reviewer="Ada Example",
            outcome="confirmed",
            today=TODAY + dt.timedelta(days=1),
        )
        preview = record_review(self.root, "C-001", **kw)
        record_review(
            self.root,
            "C-001",
            write=True,
            expected_sha256=preview["expected_sha256"],
            **kw,
        )
        self.commit()
        self.assertEqual(self.findings(base), [])
        base = self.git("rev-parse", "HEAD")
        self.path.write_text(
            _frontmatter_replace(
                self.path.read_text(encoding="utf-8"), "review_by", "2028-01-01"
            ),
            encoding="utf-8",
        )
        self.commit()
        self.assertTrue(self.findings(base))

    def test_normalized_line_endings_do_not_change_immutable_snapshot(self):
        base = self.baseline()
        p = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        self.commit()
        self.assertEqual(self.findings(base), [])

    def test_history_gate_integrates_claim_findings(self):
        base = self.baseline()
        self.path.write_text(
            _frontmatter_replace(
                self.path.read_text(encoding="utf-8"), "statement", "Changed"
            ),
            encoding="utf-8",
        )
        self.commit()
        self.assertTrue(immutability.changed_records(base, "HEAD", str(self.root)))

    def test_snapshot_type_change_to_symlink_is_rejected(self):
        import os

        if os.name == "nt":
            self.skipTest("native Windows symlink permissions vary")
        base = self.baseline()
        p = next((self.root / "00-context/claim-snapshots").glob("*.txt"))
        p.unlink()
        p.symlink_to("other.txt")
        self.commit()
        self.assertTrue(self.findings(base))

    def test_approved_claim_in_initial_staged_commit_needs_event(self):
        self.git("init", "-q")
        text = _frontmatter_replace(
            self.path.read_text(encoding="utf-8"), "status", "approved"
        )
        self.path.write_text(text, encoding="utf-8")
        self.git("add", ".")
        self.assertTrue(immutability._initial_approvals(str(self.root)))
