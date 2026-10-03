"""Team policy in whykit.toml: custom rules and built-in rule overrides.

A team states its own conventions (`[[rules.custom]]`) and tunes the severity
or scope of built-in rules (`[rules.overrides]`) without forking WhyKit. The
tests cover every check a custom rule can make, the validation of the policy
itself (each error names the offending key), the guard that keeps a
security-relevant rule from being switched off quietly, and how the new rules
travel through `rules`, `lint`, `check` and the CI formats.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _jsonschema import validate  # noqa: E402
from _vaults import fresh_vault  # noqa: E402

from whykit import cli  # noqa: E402
from whykit import lint as lint_mod  # noqa: E402
from whykit.config import ConfigError, load_config  # noqa: E402
from whykit.rule_policy import regex_problem  # noqa: E402
from whykit.rules import RULES  # noqa: E402

SCHEMAS = ROOT / "schemas"
AS_OF = dt.date(2026, 9, 17)
BASE_TOML = (
    'format_version = 1\n\n[defaults]\nowner = "Product Lead"\nsensitivity = "internal"\n'
    "decision_review_days = 90\nstatus_due_days = 30\nrequire_hub_links = false\n\n"
)


def call(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def note(
    title: str,
    *,
    doc_type: str = "research",
    status: str = "draft",
    updated: str = "2026-09-01",
    extra: str = "",
    body: str = "",
    source_ids: str = "[]",
) -> str:
    return (
        f"---\ntitle: {title}\ntype: {doc_type}\nstatus: {status}\nowner: Product Lead\n"
        f"created: 2026-01-05\nlast_updated: {updated}\nsource_of_truth: false\n"
        f"sensitivity: internal\nsource_ids: {source_ids}\ntags: []\n{extra}---\n\n# {title}\n\n{body}"
    )


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


class PolicyVault(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        fresh_vault(self.vault)
        self.linked: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def policy(self, rules: str) -> None:
        (self.vault / "whykit.toml").write_text(BASE_TOML + rules, encoding="utf-8")

    def write(self, rel: str, text: str, *, link: bool = True) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if link:
            stem = rel[:-3]
            home = self.vault / "Home.md"
            home.write_text(home.read_text(encoding="utf-8") + f"\n- [[{stem}]]\n", encoding="utf-8")
        return path

    def findings(self, prefix: str = "custom.", **kwargs: object) -> list[lint_mod.Finding]:
        _, found = lint_mod.lint(self.vault, today=AS_OF, **kwargs)  # type: ignore[arg-type]
        return [f for f in found if f.code.startswith(prefix)]

    def config_error(self, rules: str) -> str:
        self.policy(rules)
        with self.assertRaises(ConfigError) as caught:
            load_config(self.vault)
        return str(caught.exception)


class CustomRuleChecks(PolicyVault):
    def test_required_front_matter_keys(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.reviewers"\nsummary = "Research names its reviewers."\n'
                    'required_keys = ["reviewers"]\n[rules.custom.applies_to]\ntype = "research"\n')
        self.write("07-research/without.md", note("Without"))
        self.write("07-research/with.md", note("With", extra="reviewers: [Data team]\n"))
        found = self.findings()
        self.assertEqual([(f.path, f.level, f.line) for f in found], [("07-research/without.md", "warning", 1)])
        self.assertIn("reviewers", found[0].message)

    def test_required_front_matter_values_and_list_membership(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.public_tagged"\nsummary = "Public research is tagged."\n'
                    'required_values = { sensitivity = ["internal", "public"], tags = "reviewed" }\n')
        self.write("07-research/a.md", note("A").replace("tags: []", "tags: [draft, reviewed]"))
        self.write("07-research/b.md", note("B").replace("sensitivity: internal", "sensitivity: confidential"))
        messages: dict[str, str] = {}
        for f in self.findings():
            messages[f.path] = messages.get(f.path, "") + f.message
        self.assertNotIn("07-research/a.md", messages)
        self.assertIn("sensitivity is confidential", messages["07-research/b.md"])

    def test_required_sections_ignore_code_and_respect_levels(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.sections"\nsummary = "Research has its sections."\n'
                    'required_sections = ["## Method", "Limitations"]\n[rules.custom.applies_to]\ntype = "research"\n')
        self.write("07-research/ok.md", note("Ok", body="## Method\n\nx\n\n### limitations ###\n\ny\n"))
        self.write("07-research/fenced.md", note("Fenced", body="```\n## Method\n```\n\n## Limitations\n"))
        self.write("07-research/wrong-level.md", note("Wrong", body="### Method\n\n## Limitations\n"))
        found = sorted((f.path, f.message) for f in self.findings())
        self.assertEqual([path for path, _ in found], ["07-research/fenced.md", "07-research/wrong-level.md"])
        self.assertTrue(all('"## Method"' in message for _, message in found))

    def test_required_and_forbidden_patterns(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.wording"\nsummary = "Wording policy."\n'
                    'required_patterns = ["(?i)^owner sign-off:"]\nforbidden_patterns = ["\\\\bTBD\\\\b"]\n'
                    '[rules.custom.applies_to]\npaths = ["07-research/"]\n')
        self.write("07-research/good.md", note("Good", body="Owner sign-off: yes\n\nUse `TBD` for nothing.\n"))
        self.write("07-research/bad.md", note("Bad", body="Plan\n\nStill TBD here.\n"))
        found = sorted((f.path, f.line or 0, f.message) for f in self.findings())
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all(path == "07-research/bad.md" for path, _, _ in found))
        lines = {line for _, line, _ in found}
        bad_text = (self.vault / "07-research/bad.md").read_text(encoding="utf-8").splitlines()
        tbd_line = next(i for i, line in enumerate(bad_text, 1) if "TBD" in line)
        self.assertIn(tbd_line, lines)

    def test_min_evidence_for_approved_decisions_only(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.decision_evidence"\nlevel = "error"\n'
                    'summary = "Approved decisions cite two sources."\nmin_evidence = 2\n'
                    '[rules.custom.applies_to]\ntype = "decision"\nstatus = "approved"\n')
        self.write("07-research/approved.md", note("Approved", doc_type="decision", status="approved", body="Per E-001.\n"))
        self.write("07-research/draft.md", note("Draft", doc_type="decision", status="draft", body="Per E-001.\n"))
        self.write("07-research/enough.md", note("Enough", doc_type="decision", status="approved",
                                                 source_ids="[E-001]", body="Also E-002.\n"))
        found = self.findings()
        self.assertEqual([(f.path, f.level) for f in found], [("07-research/approved.md", "error")])
        self.assertIn("1 of 2", found[0].message)

    def test_max_age_since_last_updated_by_type(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.fresh_research"\nsummary = "Research is refreshed."\n'
                    'max_age_days = 30\n[rules.custom.applies_to]\ntype = ["research"]\n')
        self.write("07-research/old.md", note("Old", updated="2026-08-01"))
        self.write("07-research/new.md", note("New", updated="2026-09-01"))
        self.write("07-research/guide.md", note("Guide", doc_type="guide", updated="2026-01-10"))
        found = self.findings()
        self.assertEqual([f.path for f in found], ["07-research/old.md"])
        self.assertIn("47 days", found[0].message)

    def test_applies_to_paths_and_workstream(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.platform"\nsummary = "Platform notes name a reviewer."\n'
                    'required_keys = ["reviewers"]\n[rules.custom.applies_to]\npaths = ["07-research/**"]\n'
                    'workstream = "platform"\n')
        self.write("07-research/deep/p.md", note("P", extra="workstream: platform\n"))
        self.write("07-research/q.md", note("Q", extra="workstream: growth\n"))
        self.write("01-strategy/r.md", note("R", doc_type="strategy", extra="workstream: platform\n"))
        self.assertEqual([f.path for f in self.findings()], ["07-research/deep/p.md"])

    def test_templates_are_skipped_unless_named(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.any"\nsummary = "Everything names reviewers."\n'
                    'required_keys = ["reviewers"]\n[rules.custom.applies_to]\npaths = ["templates/**"]\n')
        self.assertEqual(self.findings(), [])
        self.policy('[[rules.custom]]\nid = "custom.any"\nsummary = "Everything names reviewers."\n'
                    'required_keys = ["reviewers"]\n[rules.custom.applies_to]\npaths = ["templates/**"]\n'
                    'status = ["template"]\n')
        self.assertTrue(self.findings())

    def test_overlong_line_is_reported_not_skipped(self) -> None:
        from whykit.rule_policy import MAX_LINE_CHARS

        self.policy('[[rules.custom]]\nid = "custom.no_tbd"\nsummary = "No TBD."\nforbidden_patterns = ["TBD"]\n')
        self.write("07-research/long.md", note("Long", body="x" * (MAX_LINE_CHARS + 5) + " TBD\n"))
        found = self.findings()
        self.assertEqual(len(found), 1)
        self.assertIn(str(MAX_LINE_CHARS), found[0].message)

    def test_scoped_lint_runs_custom_rules_on_the_named_note(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.reviewers"\nsummary = "Name reviewers."\nrequired_keys = ["reviewers"]\n'
                    '[rules.custom.applies_to]\ntype = "research"\n')
        self.write("07-research/a.md", note("A"))
        self.write("07-research/b.md", note("B"))
        _, found = lint_mod.lint(self.vault, ["07-research/a.md"], today=AS_OF)
        self.assertEqual([f.path for f in found if f.code == "custom.reviewers"], ["07-research/a.md"])

    def test_no_policy_changes_nothing(self) -> None:
        self.write("07-research/a.md", note("A"))
        before = lint_mod.lint(self.vault, today=AS_OF)[1]
        self.policy("")
        self.assertEqual(lint_mod.lint(self.vault, today=AS_OF)[1], before)


class SafeRegex(unittest.TestCase):
    def test_accepts_ordinary_patterns(self) -> None:
        for pattern in (r"\bTBD\b", r"(?i)^owner sign-off:", r"[A-Z]{2,4}-\d{3}", r"(?:foo|bar) baz",
                        r"(ab)?c+", r"https?://[^ ]+", r"(?P<id>E-\d{3,})", r"a{2}(bc){3}"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(regex_problem(pattern))

    def test_rejects_catastrophic_shapes(self) -> None:
        cases = {
            r"(a+)+$": "nested",
            r"(a*)*b": "nested",
            r"((ab)+c)+": "nested",
            r"(\w|\d)+x": "alternation",
            r"(?:a|aa){2,}": "alternation",
            r"(a)\1": "backreference",
            r"(?P<x>a)(?P=x)": "backreference",
            r"(a)?(?(1)b|c)": "conditional",
            "a" * 300: "256",
            r"(unclosed": "not a valid",
        }
        for pattern, reason in cases.items():
            with self.subTest(pattern=pattern[:20]):
                problem = regex_problem(pattern)
                self.assertIsNotNone(problem)
                self.assertIn(reason, problem)


class PolicyValidation(PolicyVault):
    def test_errors_name_the_offending_key(self) -> None:
        cases = {
            '[rules]\ncustoms = []\n': "rules: unknown keys: customs",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nrequired_keyz = ["a"]\n': "rules.custom[0]: unknown keys: required_keyz",
            '[[rules.custom]]\nid = "team.x"\nsummary = "s"\nmin_evidence = 1\n': "rules.custom[0].id",
            '[[rules.custom]]\nsummary = "s"\nmin_evidence = 1\n': "rules.custom[0].id",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 1\n'
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 1\n': "rules.custom[1].id",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\n': "rules.custom[0] (custom.x) defines no checks",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nlevel = "fatal"\nmin_evidence = 1\n': "rules.custom[0].level",
            '[[rules.custom]]\nid = "custom.x"\nmin_evidence = 1\n': "rules.custom[0].summary",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 0\n': "rules.custom[0].min_evidence",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmax_age_days = -1\n': "rules.custom[0].max_age_days",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 1\n[rules.custom.applies_to]\nstatus = "aproved"\n':
                "rules.custom[0].applies_to.status",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 1\n[rules.custom.applies_to]\ntype = ["decisions"]\n':
                "rules.custom[0].applies_to.type[0]",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 1\n[rules.custom.applies_to]\npaths = ["../x/**"]\n':
                "rules.custom[0].applies_to.paths[0]",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nforbidden_patterns = ["ok", "(a+)+"]\n': "rules.custom[0].forbidden_patterns[1]",
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nrequired_values = { status = [] }\n': "rules.custom[0].required_values.status",
            '[rules.overrides."nope.rule"]\nlevel = "off"\n': 'rules.overrides."nope.rule"',
            '[rules.overrides."note.orphan"]\nlevel = "silent"\n': 'rules.overrides."note.orphan".level',
            '[rules.overrides."note.orphan"]\nlevel = "off"\npaths = ["/abs/**"]\n': 'rules.overrides."note.orphan".paths[0]',
            '[rules.overrides."note.orphan"]\nlevel = "off"\nwhy = "x"\n': 'rules.overrides."note.orphan": unknown keys: why',
            '[[rules.overrides."note.orphan"]]\nlevel = "off"\n[[rules.overrides."note.orphan"]]\nlevl = "off"\n':
                'rules.overrides."note.orphan"[1]: unknown keys: levl',
            '[rules.overrides."config.invalid"]\nlevel = "warning"\nreason = "r"\n': 'rules.overrides."config.invalid"',
            '[rules.overrides."secret.detected"]\nlevel = "off"\npaths = ["fixtures/**"]\n': 'rules.overrides."secret.detected".reason',
            '[rules.overrides."secret.scan_unreadable"]\nlevel = "warning"\nreason = "  "\n': 'rules.overrides."secret.scan_unreadable".reason',
        }
        for rules, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, self.config_error(rules))

    def test_security_override_with_reason_and_raising_levels_are_valid(self) -> None:
        self.policy('[rules.overrides."secret.detected"]\nlevel = "off"\npaths = ["fixtures/**"]\n'
                    'reason = "Synthetic keys used by the parser tests."\n'
                    '[rules.overrides."note.orphan"]\nlevel = "error"\n'
                    '[rules.overrides."wikilink.outside"]\nlevel = "error"\n')
        config, _ = load_config(self.vault)
        self.assertIn("rules", config)

    def test_custom_rule_codes_can_be_overridden(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nmin_evidence = 1\n'
                    '[rules.overrides."custom.x"]\nlevel = "off"\npaths = ["archive/**"]\n')
        load_config(self.vault)

    def test_commands_exit_2_invalid_config_with_the_key_path(self) -> None:
        self.policy('[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nforbidden_patterns = ["(a+)+"]\n')
        for argv in (["check"], ["policy"], ["rules"], ["status"]):
            with self.subTest(argv=argv):
                code, out, err = call(*argv, "--root", str(self.vault), "--json")
                self.assertEqual(code, 2, err)
                payload = json.loads(out)
                self.assertEqual(payload["error"]["code"], "invalid_config")
                self.assertIn("rules.custom[0].forbidden_patterns[0]", payload["error"]["message"])

    def test_lint_reports_the_key_path_as_config_invalid(self) -> None:
        self.policy('[rules.overrides."secret.detected"]\nlevel = "off"\n')
        code, out, _ = call("lint", "--root", str(self.vault), "--json")
        self.assertEqual(code, 1)
        messages = [f["message"] for f in json.loads(out)["findings"] if f["code"] == "config.invalid"]
        self.assertEqual(len(messages), 1)
        self.assertIn('rules.overrides."secret.detected".reason', messages[0])

    def test_documented_example_policy_is_valid(self) -> None:
        import re

        doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
        section = doc.split("## Team rules", 1)[1]
        example = re.search(r"```toml\n(.*?)```", section, re.S).group(1)
        self.policy(example)
        config, _ = load_config(self.vault)
        self.assertEqual(len(config["rules"]["custom"]), 2)
        message = section.split("```text\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(message, self.config_error(
            '[[rules.custom]]\nid = "custom.x"\nsummary = "s"\nforbidden_patterns = ["(a+)+"]\n'))

    def test_rule_count_is_capped(self) -> None:
        from whykit.rule_policy import MAX_CUSTOM_RULES

        block = "".join(
            f'[[rules.custom]]\nid = "custom.r{i}"\nsummary = "s"\nmin_evidence = 1\n' for i in range(MAX_CUSTOM_RULES + 1)
        )
        self.assertIn(f"at most {MAX_CUSTOM_RULES}", self.config_error(block))


class Overrides(PolicyVault):
    def test_demoted_error_passes_plain_lint_but_not_strict(self) -> None:
        self.write("07-research/broken.md", note("Broken", body="See [[07-research/nowhere]].\n"))
        self.assertEqual(call("lint", "--root", str(self.vault), "--today", "2026-09-17")[0], 1)
        self.policy('[rules.overrides."wikilink.missing"]\nlevel = "warning"\n')
        self.assertEqual(call("lint", "--root", str(self.vault), "--today", "2026-09-17", "--no-orphans")[0], 0)
        self.assertEqual(call("lint", "--root", str(self.vault), "--today", "2026-09-17", "--strict")[0], 1)
        levels = {f.level for f in self.findings("wikilink.missing")}
        self.assertEqual(levels, {"warning"})

    def test_path_scoped_disable_and_first_match_wins(self) -> None:
        self.write("archive/old.md", note("Old"), link=False)
        self.write("07-research/lost.md", note("Lost"), link=False)
        self.write("07-research/keep/kept.md", note("Kept"), link=False)
        self.policy('[[rules.overrides."note.orphan"]]\nlevel = "off"\npaths = ["archive/**"]\n'
                    '[[rules.overrides."note.orphan"]]\nlevel = "error"\npaths = ["07-research/keep/*.md"]\n'
                    '[[rules.overrides."note.orphan"]]\nlevel = "off"\npaths = ["07-research/keep/**"]\n')
        found = {f.path: f.level for f in self.findings("note.orphan")}
        self.assertNotIn("archive/old.md", found)
        self.assertEqual(found["07-research/lost.md"], "warning")
        self.assertEqual(found["07-research/keep/kept.md"], "error")

    def test_promotion_fails_the_local_profile_and_custom_warning_fails_only_strict_profiles(self) -> None:
        self.write("07-research/a.md", note("A"))
        self.policy('[[rules.custom]]\nid = "custom.reviewers"\nsummary = "Name reviewers."\nrequired_keys = ["reviewers"]\n'
                    '[rules.custom.applies_to]\ntype = "research"\n')
        agents = self.vault / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace("TODO", "Decided"), encoding="utf-8")
        local = json.loads(call("check", "--root", str(self.vault), "--profile", "local", "--json", "--today", "2026-09-17")[1])
        ci = json.loads(call("check", "--root", str(self.vault), "--profile", "ci", "--json", "--today", "2026-09-17")[1])
        self.assertTrue(local["passed"], local["lint"]["findings"])
        self.assertFalse(ci["passed"])
        self.assertIn("custom.reviewers", {f["code"] for f in ci["lint"]["findings"]})
        self.policy('[[rules.custom]]\nid = "custom.reviewers"\nsummary = "Name reviewers."\nrequired_keys = ["reviewers"]\n'
                    '[rules.custom.applies_to]\ntype = "research"\n[rules.overrides."custom.reviewers"]\nlevel = "error"\n')
        local = json.loads(call("check", "--root", str(self.vault), "--profile", "local", "--json", "--today", "2026-09-17")[1])
        self.assertFalse(local["passed"])

    def test_no_overrides_key_without_policy(self) -> None:
        code, out, _ = call("lint", "--root", str(self.vault), "--json")
        self.assertNotIn("overrides", json.loads(out))


class SecurityOverrides(PolicyVault):
    REASON = "Synthetic credentials used by the parser fixtures."

    def setUp(self) -> None:
        super().setUp()
        # Split so the repository's own secret scan does not see a credential.
        self.fake = "AKIA" + "ABCDEFGHIJKLMNOP"
        self.write("fixtures/keys.md", note("Keys", body=f"Example id {self.fake}\n"))
        self.policy('[rules.overrides."secret.detected"]\nlevel = "off"\npaths = ["fixtures/**"]\n'
                    f'reason = "{self.REASON}"\n')

    def test_suppression_is_reported_in_json_text_and_check(self) -> None:
        self.assertEqual(self.findings("secret."), [])
        code, out, _ = call("lint", "--root", str(self.vault), "--json", "--today", "2026-09-17")
        payload = json.loads(out)
        self.assertEqual(validate(payload, schema("lint-report.schema.json")), [])
        self.assertNotIn("secret.detected", {f["code"] for f in payload["findings"]})
        [entry] = payload["overrides"]
        self.assertEqual((entry["rule"], entry["level"], entry["security"], entry["matched"]),
                         ("secret.detected", "off", True, 1))
        self.assertEqual(entry["reason"], self.REASON)
        self.assertEqual([s["path"] for s in entry["suppressed"]], ["fixtures/keys.md"])
        self.assertNotIn(self.fake, out)

        code, text, _ = call("lint", "--root", str(self.vault), "--today", "2026-09-17", "--quiet")
        self.assertIn("secret.detected", text)
        self.assertIn(self.REASON, text)

        code, out, _ = call("check", "--root", str(self.vault), "--profile", "local", "--json", "--today", "2026-09-17")
        report = json.loads(out)
        self.assertEqual(validate(report, schema("check-report.schema.json")), [])
        self.assertEqual(report["lint"]["overrides"][0]["matched"], 1)
        code, text, _ = call("check", "--root", str(self.vault), "--profile", "local", "--today", "2026-09-17")
        self.assertIn(self.REASON, text)

    def test_ci_formats_carry_the_suppression(self) -> None:
        code, out, _ = call("lint", "--root", str(self.vault), "--format", "sarif", "--today", "2026-09-17")
        sarif = json.loads(out)
        self.assertEqual(validate(sarif, schema("lint-sarif.schema.json")), [])
        suppressed = [r for r in sarif["runs"][0]["results"] if r.get("suppressions")]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0]["ruleId"], "secret.detected")
        self.assertEqual(suppressed[0]["suppressions"][0]["justification"], self.REASON)
        code, out, _ = call("lint", "--root", str(self.vault), "--format", "github", "--today", "2026-09-17")
        notices = [line for line in out.splitlines() if line.startswith("::notice")]
        self.assertEqual(len(notices), 1)
        self.assertIn("secret.detected", notices[0])

    def test_findings_outside_the_scope_still_fail(self) -> None:
        self.write("07-research/leak.md", note("Leak", body=f"Example id {self.fake}\n"))
        self.assertEqual([f.path for f in self.findings("secret.")], ["07-research/leak.md"])


class SecretScanSkipped(PolicyVault):
    """Switching the whole secret scan off is reported like a security override."""

    def setUp(self) -> None:
        super().setUp()
        # Split so the repository's own secret scan does not see a credential.
        self.write("07-research/keys.md", note("Keys", body="Example id " + "AKIA" + "ABCDEFGHIJKLMNOP" + "\n"))

    def test_no_secrets_flag_is_reported_in_text_json_and_ci_formats(self) -> None:
        code, text, _ = call("lint", "--root", str(self.vault), "--no-secrets", "--quiet", "--today", "2026-09-17")
        self.assertIn("policy: secret scan skipped by --no-secrets", text)

        code, out, _ = call("lint", "--root", str(self.vault), "--no-secrets", "--json", "--today", "2026-09-17")
        payload = json.loads(out)
        self.assertEqual(validate(payload, schema("lint-report.schema.json")), [])
        self.assertNotIn("secret.detected", {f["code"] for f in payload["findings"]})
        [entry] = payload["overrides"]
        self.assertEqual(
            (entry["rule"], entry["level"], entry["security"], entry["skipped_by"], entry["matched"]),
            ("secret.*", "off", True, "--no-secrets", 0),
        )

        code, out, _ = call("lint", "--root", str(self.vault), "--no-secrets", "--format", "github", "--today", "2026-09-17")
        notices = [line for line in out.splitlines() if line.startswith("::notice")]
        self.assertEqual(len(notices), 1)
        self.assertIn("secret scan skipped by --no-secrets", notices[0])

        code, out, _ = call("lint", "--root", str(self.vault), "--no-secrets", "--format", "sarif", "--today", "2026-09-17")
        sarif = json.loads(out)
        self.assertEqual(validate(sarif, schema("lint-sarif.schema.json")), [])
        [invocation] = sarif["runs"][0]["invocations"]
        [notice] = invocation["toolConfigurationNotifications"]
        self.assertEqual(notice["descriptor"]["id"], "secret.*")
        self.assertIn("--no-secrets", notice["message"]["text"])

    def test_a_scanning_run_reports_nothing(self) -> None:
        code, out, _ = call("lint", "--root", str(self.vault), "--json", "--today", "2026-09-17")
        self.assertNotIn("overrides", json.loads(out))
        code, out, _ = call("lint", "--root", str(self.vault), "--format", "sarif", "--today", "2026-09-17")
        self.assertNotIn("invocations", json.loads(out)["runs"][0])

    def test_a_profile_without_the_scan_is_reported_by_check(self) -> None:
        self.policy(
            '[profiles.quick]\nstrict = false\norphans = false\nsecrets = false\nrequire_git = false\n'
            'require_clean_tree = false\nrequire_configured = false\nrequire_hub_links = false\nhistory = "off"\n'
        )
        code, out, _ = call("check", "--root", str(self.vault), "--profile", "quick", "--json", "--today", "2026-09-17")
        report = json.loads(out)
        self.assertEqual(validate(report, schema("check-report.schema.json")), [])
        [entry] = report["lint"]["overrides"]
        self.assertEqual(entry["skipped_by"], "profile quick (secrets = false)")
        code, text, _ = call("check", "--root", str(self.vault), "--profile", "quick", "--today", "2026-09-17")
        self.assertIn("policy  secret scan skipped by profile quick", text)
        code, out, _ = call("check", "--root", str(self.vault), "--profile", "quick", "--format", "github",
                            "--today", "2026-09-17")
        self.assertTrue(any(line.startswith("::notice") and "secret scan skipped" in line for line in out.splitlines()))


class RuleCatalog(PolicyVault):
    POLICY = ('[[rules.custom]]\nid = "custom.decision_evidence"\nlevel = "error"\n'
              'summary = "Approved decisions cite two sources."\nwhy = "One source is an anecdote."\n'
              'fix = "Cite a second E-NNN."\nmin_evidence = 2\n'
              '[rules.custom.applies_to]\ntype = "decision"\nstatus = "approved"\n'
              '[rules.overrides."note.orphan"]\nlevel = "off"\npaths = ["archive/**"]\n')

    def test_rules_lists_custom_rules_and_overrides(self) -> None:
        self.policy(self.POLICY)
        code, out, _ = call("rules", "--root", str(self.vault), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(validate(payload, schema("rule-catalog.schema.json")), [])
        custom = [rule for rule in payload["rules"] if rule.get("custom")]
        self.assertEqual([rule["code"] for rule in custom], ["custom.decision_evidence"])
        self.assertEqual(custom[0]["applies_to"], {"type": ["decision"], "status": ["approved"]})
        self.assertEqual(custom[0]["checks"], {"min_evidence": 2})
        self.assertEqual(payload["count"], len(payload["rules"]))
        self.assertEqual(payload["overrides"][0]["rule"], "note.orphan")

        code, out, _ = call("rules", "custom.decision_evidence", "--root", str(self.vault), "--json")
        self.assertEqual(code, 0)
        detail = json.loads(out)
        self.assertEqual(validate(detail, schema("rule-detail.schema.json")), [])
        self.assertEqual(detail["why"], "One source is an anecdote.")

        code, text, _ = call("rules", "--root", str(self.vault))
        self.assertIn("custom.decision_evidence", text)
        self.assertIn("note.orphan", text.split("custom.decision_evidence", 1)[1])

    def test_markdown_appends_the_team_policy_after_the_builtin_table(self) -> None:
        builtin = call("rules", "--markdown")[1]
        self.policy(self.POLICY)
        code, out, _ = call("rules", "--markdown", "--root", str(self.vault))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(builtin.rstrip("\n")))
        tail = out[len(builtin.rstrip("\n")):]
        self.assertIn("`custom.decision_evidence`", tail)
        self.assertIn("`note.orphan`", tail)

    def test_builtin_catalog_flags_security_rules(self) -> None:
        payload = json.loads(call("rules", "--json")[1])
        security = {rule["code"] for rule in payload["rules"] if rule["security"]}
        self.assertTrue({"secret.detected", "secret.scan_unreadable"} <= security)
        self.assertEqual(security, {rule.code for rule in RULES if rule.security})
        doc = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
        for code in security:
            self.assertIn(f"`{code}`", doc.split("<!-- rules:end -->", 1)[1])

    def test_custom_findings_in_sarif_name_their_rule(self) -> None:
        self.policy(self.POLICY)
        self.write("07-research/d.md", note("D", doc_type="decision", status="approved", body="E-001\n"))
        code, out, _ = call("lint", "--root", str(self.vault), "--format", "sarif")
        run = json.loads(out)["runs"][0]
        rules = {rule["id"]: rule for rule in run["tool"]["driver"]["rules"]}
        self.assertEqual(rules["custom.decision_evidence"]["shortDescription"]["text"], "Approved decisions cite two sources.")
        self.assertNotIn("helpUri", rules["custom.decision_evidence"])
        self.assertIn("custom.decision_evidence", {r["ruleId"] for r in run["results"]})
        code, out, _ = call("lint", "--root", str(self.vault), "--format", "github")
        line = next(line for line in out.splitlines() if "custom.decision_evidence" in line)
        self.assertNotIn("docs/rules.md", line)


@unittest.skipIf(__import__("os").environ.get("WHYKIT_SKIP_PERF"), "WHYKIT_SKIP_PERF is set")
class PolicyPerformance(unittest.TestCase):
    def test_a_realistic_policy_keeps_lint_within_budget(self) -> None:
        from synthetic_vault import generate

        with tempfile.TemporaryDirectory() as tmp:
            vault = generate(Path(tmp) / "vault", 1000)
            config = (vault / "whykit.toml").read_text(encoding="utf-8")

            def timed() -> tuple[float, list[lint_mod.Finding]]:
                started = time.perf_counter()
                _, found = lint_mod.lint(vault, today=AS_OF)
                return time.perf_counter() - started, found

            baseline, plain = timed()
            (vault / "whykit.toml").write_text(config + (
                '\n[[rules.custom]]\nid = "custom.sections"\nsummary = "s"\nrequired_sections = ["Context", "Decision"]\n'
                'forbidden_patterns = ["\\\\bTBD\\\\b", "(?i)lorem ipsum dolor", "[A-Z]{3}-\\\\d{5}"]\n'
                'required_patterns = ["(?i)owner sign-off"]\n'
                '[rules.custom.applies_to]\ntype = ["decision", "research", "strategy"]\n'
                '[[rules.custom]]\nid = "custom.evidence"\nsummary = "s"\nmin_evidence = 1\nmax_age_days = 365\n'
                'required_keys = ["owner", "reviewers"]\n'
                '[rules.overrides."note.orphan"]\nlevel = "off"\npaths = ["07-research/**", "reports/**"]\n'
                '[rules.overrides."evidence.retired"]\nlevel = "error"\n'
            ), encoding="utf-8")
            with_policy, found = timed()
        self.assertTrue(any(f.code.startswith("custom.") for f in found))
        self.assertLess(with_policy, max(baseline * 2.5, baseline + 1.5),
                        f"lint took {with_policy:.2f}s with the policy vs {baseline:.2f}s without")
        self.assertTrue(plain)


if __name__ == "__main__":
    unittest.main()
