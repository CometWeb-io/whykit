"""Team policy declared in ``whykit.toml``: custom rules and rule overrides.

Two optional tables extend the built-in linter without forking it::

    [[rules.custom]]                    # a team convention, reported as custom.<name>
    id = "custom.decision_evidence"
    level = "error"
    summary = "Approved decisions cite at least two sources."
    min_evidence = 2
    [rules.custom.applies_to]
    type = "decision"
    status = "approved"

    [rules.overrides."note.orphan"]     # retune a rule, optionally for some paths only
    level = "off"
    paths = ["99-archive/**"]

Everything here is validated up front, and every error names the key that is
wrong (``rules.custom[0].min_evidence``), because a policy that half-applies is
worse than none. Two guards keep the policy itself from becoming a hole:

* patterns run on Python's backtracking engine, which has no timeout, so a
  pattern that can backtrack catastrophically is rejected and the text it is
  matched against is bounded (one line at a time, ``MAX_LINE_CHARS`` each);
* a security-relevant rule (``Rule.security``) cannot be lowered or switched
  off without a written ``reason``, and every such override is reported in the
  lint and check output together with the findings it suppressed.

This module must not import :mod:`whykit.lint` at import time: ``lint`` imports
``config``, which imports this module.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .rules import RULE_BY_CODE

if TYPE_CHECKING:  # pragma: no cover
    from .lint import Finding, Note

CUSTOM_ID_RE = re.compile(r"^custom\.[a-z0-9_]{1,60}$")
FRONT_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
LEVELS = ("error", "warning")
OVERRIDE_LEVELS = ("error", "warning", "off")
# A rule that guards the policy itself: overriding it could only hide that
# the policy is broken.
NOT_OVERRIDABLE = frozenset({"config.invalid"})

MAX_CUSTOM_RULES = 100
MAX_LIST_ITEMS = 50
MAX_PATTERNS = 20
MAX_PATTERN_LENGTH = 256
MAX_TEXT_LENGTH = 500
MAX_OVERRIDE_ENTRIES = 50
# Patterns are matched one line at a time. A longer line is checked on its
# first MAX_LINE_CHARS characters and the truncation is reported, so a pattern
# cannot be made to run for minutes and a skipped tail is never silent.
MAX_LINE_CHARS = 10_000

APPLIES_TO_KEYS = ("type", "status", "paths", "workstream")
CHECK_KEYS = (
    "required_keys",
    "required_values",
    "required_sections",
    "required_patterns",
    "forbidden_patterns",
    "min_evidence",
    "max_age_days",
)
CUSTOM_KEYS = frozenset({"id", "level", "summary", "why", "fix", "applies_to", *CHECK_KEYS})
RULES_KEYS = frozenset({"custom", "overrides"})
OVERRIDE_KEYS = frozenset({"level", "paths", "reason"})

DEFAULT_WHY = "A team convention defined in this vault's whykit.toml."
DEFAULT_FIX = "Follow the team policy stated in whykit.toml."


class PolicyError(ValueError):
    """An invalid ``[rules]`` table. The message starts with the key path."""


# --------------------------------------------------------------------------
# Safe regular expressions
# --------------------------------------------------------------------------

_BRACE_RE = re.compile(r"\{(\d*)(?:(,)(\d*))?\}")


@dataclass
class _Frame:
    repeats: bool = False      # holds a quantifier that can match more than once
    alternates: bool = False   # holds a top-level or nested alternation


def _skip_group_prefix(pattern: str, at: int) -> tuple[int, str]:
    """Return (index after the group's prefix, kind) for a group opening at *at*.

    *kind* is ``group`` for a capturing/non-capturing/lookaround group,
    ``flags`` for a flag-only group such as ``(?i)``, ``comment``,
    ``backreference`` or ``conditional``.
    """
    i = at + 1
    if pattern[i:i + 1] != "?":
        return i, "group"
    rest = pattern[i + 1:]
    if rest.startswith("P="):
        return i, "backreference"
    if rest.startswith("("):
        return i, "conditional"
    if rest.startswith("#"):
        end = pattern.find(")", i)
        return (len(pattern) if end < 0 else end + 1), "comment"
    if rest.startswith("P<"):
        end = pattern.find(">", i)
        return (len(pattern) if end < 0 else end + 1), "group"
    if rest.startswith(("<=", "<!")):
        return i + 3, "group"
    if rest.startswith(("=", "!", ":", ">")):
        return i + 2, "group"
    j = i + 1
    while j < len(pattern) and pattern[j] not in ":)":
        j += 1
    if j < len(pattern) and pattern[j] == ")":
        return j + 1, "flags"
    return j + 1, "group"


def _quantifier(pattern: str, at: int) -> tuple[int, bool] | None:
    """Return (length, can_repeat) for a quantifier at *at*, else ``None``."""
    char = pattern[at]
    if char in "*+":
        return 1, True
    if char == "?":
        return 1, False
    if char == "{":
        match = _BRACE_RE.match(pattern, at)
        if match is None or not (match.group(1) or match.group(3)):
            return None  # a literal brace
        low, comma, high = match.group(1), match.group(2), match.group(3)
        if comma is None:
            repeat = int(low) > 1
        else:
            repeat = not high or int(high) > 1
        return match.end() - at, repeat
    return None


def regex_problem(pattern: str) -> str | None:
    """Why *pattern* is not acceptable in a policy, or ``None`` when it is.

    Rejected: patterns longer than ``MAX_PATTERN_LENGTH``, patterns that do not
    compile, backreferences, conditional groups, a repeated group that itself
    holds a repeating quantifier (``(a+)+``), and a repeated group that holds
    an alternation (``(a|aa)*``). Those are the shapes that make a backtracking
    engine take exponential time on a short input.
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        return f"is longer than {MAX_PATTERN_LENGTH} characters"
    if not pattern:
        return "is empty"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"is not a valid regular expression: {exc}"
    frames = [_Frame()]
    last_group: _Frame | None = None
    last_is_atom = False
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "\\":
            follower = pattern[i + 1:i + 2]
            if follower.isdigit() and follower != "0":
                return "uses a backreference, which can take exponential time"
            i += 2
            last_group, last_is_atom = None, True
            continue
        if char == "[":
            j = i + 1
            if pattern[j:j + 1] == "^":
                j += 1
            if pattern[j:j + 1] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            i = j + 1
            last_group, last_is_atom = None, True
            continue
        if char == "(":
            after, kind = _skip_group_prefix(pattern, i)
            if kind == "backreference":
                return "uses a backreference, which can take exponential time"
            if kind == "conditional":
                return "uses a conditional group, which refers back to another group"
            if kind in ("comment", "flags"):
                i = after
                last_group, last_is_atom = None, False
                continue
            frames.append(_Frame())
            i = after
            last_group, last_is_atom = None, False
            continue
        if char == ")":
            closed = frames.pop() if len(frames) > 1 else _Frame()
            parent = frames[-1]
            parent.repeats |= closed.repeats
            parent.alternates |= closed.alternates
            i += 1
            last_group, last_is_atom = closed, True
            continue
        if char == "|":
            frames[-1].alternates = True
            i += 1
            last_group, last_is_atom = None, False
            continue
        quantifier = _quantifier(pattern, i)
        if quantifier is not None and last_is_atom:
            length, repeats = quantifier
            if repeats and last_group is not None:
                if last_group.repeats:
                    return "has a nested quantifier (a quantifier inside a repeated group), which can take exponential time"
                if last_group.alternates:
                    return "repeats a group that contains an alternation, which can take exponential time"
            if repeats:
                frames[-1].repeats = True
            i += length
            if i < n and pattern[i] in "?+":  # lazy or possessive suffix
                i += 1
            last_group, last_is_atom = None, False
            continue
        i += 1
        last_group, last_is_atom = None, True
    return None


# --------------------------------------------------------------------------
# Path globs
# --------------------------------------------------------------------------

def _glob_problem(pattern: str) -> str | None:
    if not pattern.strip():
        return "must be a non-empty glob"
    if len(pattern) > MAX_TEXT_LENGTH:
        return f"is longer than {MAX_TEXT_LENGTH} characters"
    if pattern.startswith("/") or "\\" in pattern or re.match(r"^[A-Za-z]:", pattern):
        return "must be a vault-relative POSIX glob such as 07-research/**"
    if ".." in pattern.split("/"):
        return "may not leave the vault with '..'"
    return None


def compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a vault-relative glob into a regular expression.

    ``*`` and ``?`` stay within one path segment, ``**`` crosses segments, and
    ``dir/`` means everything below ``dir``.
    """
    if pattern.endswith("/"):
        pattern += "**"
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]*/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


# --------------------------------------------------------------------------
# Compiled policy
# --------------------------------------------------------------------------

@dataclass
class CustomRule:
    code: str
    level: str
    summary: str
    why: str
    fix: str
    applies_to: dict[str, list[str]]
    checks: dict[str, Any]
    path_globs: tuple[re.Pattern[str], ...] = ()
    required: tuple[tuple[str, re.Pattern[str]], ...] = ()
    forbidden: tuple[tuple[str, re.Pattern[str]], ...] = ()

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "default_level": self.level,
            "summary": self.summary,
            "why": self.why,
            "fix": self.fix,
            "security": False,
            "custom": True,
            "applies_to": self.applies_to,
            "checks": self.checks,
        }


@dataclass
class Override:
    rule: str
    level: str
    paths: list[str]
    reason: str | None
    security: bool
    globs: tuple[re.Pattern[str], ...] = ()
    matched: int = 0
    suppressed: list[Finding] = field(default_factory=list)
    # Set when a run switched the whole secret scan off (``--no-secrets`` or a
    # profile with ``secrets = false``) rather than a whykit.toml override.
    skipped_by: str | None = None

    def applies(self, path: str) -> bool:
        return not self.globs or any(glob.match(path) for glob in self.globs)

    def report(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "rule": self.rule,
            "level": self.level,
            "paths": list(self.paths),
            "reason": self.reason,
            "security": self.security,
            "matched": self.matched,
        }
        if self.security:
            from dataclasses import asdict

            entry["suppressed"] = [asdict(item) for item in self.suppressed]
        if self.skipped_by:
            entry["skipped_by"] = self.skipped_by
        return entry

    def describe(self) -> str:
        return describe_override(self.report())


def describe_override(entry: dict[str, Any]) -> str:
    """One line for an override report entry, as lint and check print it."""
    if entry.get("skipped_by"):
        return f"secret scan skipped by {entry['skipped_by']}; secret.* findings were not collected"
    scope = ", ".join(entry["paths"]) if entry["paths"] else "the whole vault"
    verb = "switched off" if entry["level"] == "off" else f"set to {entry['level']}"
    counted = "suppressed" if entry["level"] == "off" else "changed"
    noun = "finding" if entry["matched"] == 1 else "findings"
    text = f"{entry['rule']} {verb} for {scope} ({entry['matched']} {noun} {counted})"
    return text + (f" — reason: {entry['reason']}" if entry["reason"] else "")


SECRET_SCAN_RULE = "secret.*"


def secret_scan_skipped(source: str) -> Override:
    """The report entry for a run whose secret scan was switched off.

    Disabling the scan is the bluntest way to lower a security-relevant rule,
    so it is reported like a security override: printed with every run and
    listed in the JSON ``overrides`` array, never silent.
    """
    return Override(
        rule=SECRET_SCAN_RULE,
        level="off",
        paths=[],
        reason=f"secret scan skipped by {source}",
        security=True,
        skipped_by=source,
    )


@dataclass
class RulePolicy:
    custom: list[CustomRule]
    overrides: list[Override]

    def apply_overrides(self, findings: list[Finding]) -> list[Finding]:
        """Return *findings* with override levels applied and 'off' entries removed.

        Override entries for one rule are tried in the order they are written;
        the first whose ``paths`` match the finding (or that has no ``paths``)
        decides. Counters on each entry record what it changed.
        """
        if not self.overrides:
            return findings
        by_rule: dict[str, list[Override]] = {}
        for item in self.overrides:
            item.matched = 0
            item.suppressed = []
            by_rule.setdefault(item.rule, []).append(item)
        kept: list[Finding] = []
        for finding in findings:
            entries = by_rule.get(finding.code, ())
            entry = next((e for e in entries if e.applies(finding.path)), None)
            if entry is None:
                kept.append(finding)
                continue
            if entry.level == "off":
                entry.matched += 1
                entry.suppressed.append(finding)
                continue
            if entry.level != finding.level:
                entry.matched += 1
                finding.level = entry.level
            kept.append(finding)
        return kept


EMPTY_POLICY = RulePolicy([], [])


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _quote(code: str) -> str:
    return f'"{code}"'


def _strings(value: object, where: str, *, allow_scalar: bool = True) -> list[str]:
    if allow_scalar and isinstance(value, str):
        items: list[object] = [value]
        indexed = False
    elif isinstance(value, list):
        items = list(value)
        indexed = True
    else:
        raise PolicyError(f"{where} must be a string or a list of strings")
    if not items:
        raise PolicyError(f"{where} must not be empty")
    if len(items) > MAX_LIST_ITEMS:
        raise PolicyError(f"{where} may hold at most {MAX_LIST_ITEMS} items")
    out: list[str] = []
    for index, item in enumerate(items):
        at = f"{where}[{index}]" if indexed else where
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(f"{at} must be a non-empty string")
        if len(item) > MAX_TEXT_LENGTH:
            raise PolicyError(f"{at} is longer than {MAX_TEXT_LENGTH} characters")
        out.append(item)
    return out


def _indexed(where: str, index: int, single: bool) -> str:
    return where if single else f"{where}[{index}]"


def _scalar_text(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)) and not (isinstance(value, str) and not value.strip()):
        return str(value)
    return None


def _check_vocabulary(values: list[str], allowed: Iterable[str], where: str, single: bool, noun: str) -> None:
    allowed_set = set(allowed)
    for index, value in enumerate(values):
        if value not in allowed_set:
            at = _indexed(where, index, single)
            raise PolicyError(f"{at}: unknown {noun} {value!r}; use one of {', '.join(sorted(allowed_set))}")


def _custom_rule(raw: object, index: int, seen: set[str]) -> CustomRule:
    from .lint import ALLOWED_STATUS, ALLOWED_TYPE

    where = f"rules.custom[{index}]"
    if not isinstance(raw, dict):
        raise PolicyError(f"{where} must be a table ([[rules.custom]])")
    unknown = set(raw) - CUSTOM_KEYS
    if unknown:
        raise PolicyError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")
    code = raw.get("id")
    if code is None:
        raise PolicyError(f"{where}.id is required")
    if not isinstance(code, str) or not CUSTOM_ID_RE.match(code):
        raise PolicyError(f"{where}.id must look like custom.<name> (lowercase letters, digits and _), got {code!r}")
    if code in seen:
        raise PolicyError(f"{where}.id: {code} is defined twice")
    seen.add(code)
    label = f"{where} ({code})"

    level = raw.get("level", "warning")
    if level not in LEVELS:
        raise PolicyError(f"{where}.level must be error or warning")
    texts: dict[str, str] = {}
    for key, default in (("summary", None), ("why", DEFAULT_WHY), ("fix", DEFAULT_FIX)):
        value = raw.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise PolicyError(f"{where}.{key} must be a non-empty string")
        if len(value) > MAX_TEXT_LENGTH:
            raise PolicyError(f"{where}.{key} is longer than {MAX_TEXT_LENGTH} characters")
        texts[key] = value.strip()

    applies_to: dict[str, list[str]] = {}
    raw_scope = raw.get("applies_to", {})
    if not isinstance(raw_scope, dict):
        raise PolicyError(f"{where}.applies_to must be a table")
    unknown = set(raw_scope) - set(APPLIES_TO_KEYS)
    if unknown:
        raise PolicyError(f"{where}.applies_to: unknown keys: {', '.join(sorted(unknown))}")
    for key in APPLIES_TO_KEYS:
        if key not in raw_scope:
            continue
        at = f"{where}.applies_to.{key}"
        values = _strings(raw_scope[key], at)
        single = isinstance(raw_scope[key], str)
        if key == "type":
            _check_vocabulary(values, ALLOWED_TYPE, at, single, "type")
        elif key == "status":
            _check_vocabulary(values, ALLOWED_STATUS, at, single, "status")
        elif key == "paths":
            for position, glob in enumerate(values):
                problem = _glob_problem(glob)
                if problem:
                    raise PolicyError(f"{_indexed(at, position, single)} {problem}")
        applies_to[key] = values

    checks: dict[str, Any] = {}
    if "required_keys" in raw:
        at = f"{where}.required_keys"
        keys = _strings(raw["required_keys"], at, allow_scalar=False)
        for position, key in enumerate(keys):
            if not FRONT_KEY_RE.match(key):
                raise PolicyError(f"{at}[{position}] is not a front-matter key: {key!r}")
        checks["required_keys"] = keys
    if "required_values" in raw:
        at = f"{where}.required_values"
        table = raw["required_values"]
        if not isinstance(table, dict) or not table:
            raise PolicyError(f"{at} must be a non-empty table of key = value(s)")
        values_out: dict[str, list[str]] = {}
        for key, value in table.items():
            if not FRONT_KEY_RE.match(key):
                raise PolicyError(f"{at}: {key!r} is not a front-matter key")
            items = value if isinstance(value, list) else [value]
            texts_out = [_scalar_text(item) for item in items]
            if not items or len(items) > MAX_LIST_ITEMS or any(text is None for text in texts_out):
                raise PolicyError(f"{at}.{key} must be a value or a non-empty list of values (strings, numbers or booleans)")
            values_out[key] = [text for text in texts_out if text is not None]
        checks["required_values"] = values_out
    if "required_sections" in raw:
        at = f"{where}.required_sections"
        sections = _strings(raw["required_sections"], at, allow_scalar=False)
        for position, section in enumerate(sections):
            if _parse_section(section)[1] == "":
                raise PolicyError(f"{at}[{position}] must name a heading, e.g. \"## Context\" or \"Context\"")
        checks["required_sections"] = sections
    compiled: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {}
    for key in ("required_patterns", "forbidden_patterns"):
        if key not in raw:
            continue
        at = f"{where}.{key}"
        patterns = _strings(raw[key], at, allow_scalar=False)
        if len(patterns) > MAX_PATTERNS:
            raise PolicyError(f"{at} may hold at most {MAX_PATTERNS} patterns")
        for position, pattern in enumerate(patterns):
            problem = regex_problem(pattern)
            if problem:
                raise PolicyError(f"{at}[{position}] {problem}: /{pattern[:60]}/")
        compiled[key] = tuple((pattern, re.compile(pattern)) for pattern in patterns)
        checks[key] = patterns
    for key, minimum in (("min_evidence", 1), ("max_age_days", 0)):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > 100_000:
            raise PolicyError(f"{where}.{key} must be an integer >= {minimum}")
        checks[key] = value
    if not checks:
        raise PolicyError(f"{label} defines no checks; add at least one of {', '.join(CHECK_KEYS)}")

    return CustomRule(
        code=code,
        level=level,
        summary=texts["summary"],
        why=texts["why"],
        fix=texts["fix"],
        applies_to=applies_to,
        checks=checks,
        path_globs=tuple(compile_glob(glob) for glob in applies_to.get("paths", [])),
        required=compiled.get("required_patterns", ()),
        forbidden=compiled.get("forbidden_patterns", ()),
    )


def _override_entries(code: str, raw: object, custom_codes: set[str]) -> list[Override]:
    where = f"rules.overrides.{_quote(code)}"
    if code in NOT_OVERRIDABLE:
        raise PolicyError(f"{where}: {code} cannot be overridden; fix whykit.toml instead")
    builtin = RULE_BY_CODE.get(code)
    if builtin is None and code not in custom_codes:
        raise PolicyError(f"{where}: unknown rule code; see `whykit rules` for the catalog")
    security = bool(builtin and builtin.security)
    if isinstance(raw, dict):
        tables: list[tuple[str, object]] = [(where, raw)]
    elif isinstance(raw, list) and raw:
        if len(raw) > MAX_OVERRIDE_ENTRIES:
            raise PolicyError(f"{where} may hold at most {MAX_OVERRIDE_ENTRIES} entries")
        tables = [(f"{where}[{index}]", item) for index, item in enumerate(raw)]
    else:
        raise PolicyError(f"{where} must be a table or an array of tables")
    entries: list[Override] = []
    for at, table in tables:
        if not isinstance(table, dict):
            raise PolicyError(f"{at} must be a table")
        unknown = set(table) - OVERRIDE_KEYS
        if unknown:
            raise PolicyError(f"{at}: unknown keys: {', '.join(sorted(unknown))}")
        level = table.get("level")
        if level not in OVERRIDE_LEVELS:
            raise PolicyError(f"{at}.level must be error, warning or off")
        paths: list[str] = []
        if "paths" in table:
            paths = _strings(table["paths"], f"{at}.paths", allow_scalar=False)
            for position, glob in enumerate(paths):
                problem = _glob_problem(glob)
                if problem:
                    raise PolicyError(f"{at}.paths[{position}] {problem}")
        reason = table.get("reason")
        if reason is not None:
            if not isinstance(reason, str):
                raise PolicyError(f"{at}.reason must be a string")
            if len(reason) > MAX_TEXT_LENGTH:
                raise PolicyError(f"{at}.reason is longer than {MAX_TEXT_LENGTH} characters")
            reason = reason.strip() or None
        default_level = builtin.default_level if builtin else None
        lowers = level == "off" or (level == "warning" and default_level == "error")
        if security and lowers and not reason:
            raise PolicyError(
                f"{at}.reason is required: {code} is security-relevant, so lowering or switching it off "
                "must say why (the reason is printed with every lint and check run)"
            )
        entries.append(Override(
            rule=code,
            level=level,
            paths=paths,
            reason=reason,
            security=security,
            globs=tuple(compile_glob(glob) for glob in paths),
        ))
    return entries


def build_policy(rules: object) -> RulePolicy:
    """Validate a ``[rules]`` table and compile it. Raises :class:`PolicyError`."""
    if rules is None:
        return EMPTY_POLICY
    if not isinstance(rules, dict):
        raise PolicyError("rules must be a table ([rules])")
    unknown = set(rules) - RULES_KEYS
    if unknown:
        raise PolicyError(f"rules: unknown keys: {', '.join(sorted(unknown))}")
    custom_raw = rules.get("custom", [])
    if not isinstance(custom_raw, list):
        raise PolicyError("rules.custom must be an array of tables ([[rules.custom]])")
    if len(custom_raw) > MAX_CUSTOM_RULES:
        raise PolicyError(f"rules.custom may define at most {MAX_CUSTOM_RULES} rules")
    seen: set[str] = set()
    custom = [_custom_rule(raw, index, seen) for index, raw in enumerate(custom_raw)]
    overrides_raw = rules.get("overrides", {})
    if not isinstance(overrides_raw, dict):
        raise PolicyError('rules.overrides must be a table ([rules.overrides."<code>"])')
    overrides: list[Override] = []
    for code, raw in overrides_raw.items():
        overrides.extend(_override_entries(code, raw, seen))
    return RulePolicy(custom, overrides)


def policy_from_config(config: dict[str, Any]) -> RulePolicy:
    """The compiled policy of an already validated config."""
    return build_policy(config.get("rules"))


# --------------------------------------------------------------------------
# Checking notes
# --------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*(?:#+[ \t]*)?$")


def _parse_section(text: str) -> tuple[int | None, str]:
    match = re.match(r"^(#{1,6})[ \t]+(.*)$", text.strip())
    if match:
        level, title = len(match.group(1)), match.group(2)
    else:
        level, title = None, text
    title = re.sub(r"[ \t]+#+$", "", title.strip()).strip()
    return level, title.casefold()


def _front_text(value: object) -> list[str]:
    if isinstance(value, list):
        return [text for text in (_scalar_text(item) for item in value) if text is not None]
    text = _scalar_text(value)
    return [text] if text is not None else []


def _matches_scope(rule: CustomRule, note: Note, path: str) -> bool:
    if not note.has_front or note.front_error:
        return False
    front = note.front
    statuses = rule.applies_to.get("status")
    status = str(front.get("status", ""))
    if statuses is None:
        if status == "template":
            return False
    elif status not in statuses:
        return False
    types = rule.applies_to.get("type")
    if types is not None and str(front.get("type", "")) not in types:
        return False
    streams = rule.applies_to.get("workstream")
    if streams is not None and not set(_front_text(front.get("workstream"))) & set(streams):
        return False
    if rule.path_globs and not any(glob.match(path) for glob in rule.path_globs):
        return False
    return True


def _body_lines(note: Note) -> list[tuple[int, str]]:
    """(1-based file line, masked text) for every body line."""
    lines = note.masked.split("\n")
    start = note.body_offset if note.has_front else 0
    return [(number + 1, lines[number]) for number in range(start, len(lines))]


def check_custom_rules(
    policy: RulePolicy,
    notes: Iterable[Note],
    findings: list[Finding],
    today: dt.date,
    relative: Any,
) -> None:
    """Run every custom rule against the notes it applies to.

    *relative* maps a note path to its vault-relative POSIX path (``lint.rel``
    bound to the root) so findings and path globs use the same spelling.
    """
    if not policy.custom:
        return
    from .lint import Finding, _parse_date

    for note in notes:
        path = relative(note.path)
        rules = [rule for rule in policy.custom if _matches_scope(rule, note, path)]
        if not rules:
            continue
        lines: list[tuple[int, str]] | None = None
        headings: list[tuple[int, str]] | None = None
        for rule in rules:
            def report(line: int | None, message: str, rule: CustomRule = rule, path: str = path) -> None:
                findings.append(Finding(path, line, rule.level, rule.code, message))

            checks = rule.checks
            front = note.front
            for key in checks.get("required_keys", ()):
                value = front.get(key)
                if key not in front or value is None or value == "" or value == []:
                    report(1, f"front matter needs {key!r} ({rule.summary})")
            for key, allowed in checks.get("required_values", {}).items():
                actual = _front_text(front.get(key))
                if not actual:
                    report(1, f"front matter needs {key!r} set to {' or '.join(allowed)} ({rule.summary})")
                elif not set(actual) & set(allowed):
                    report(1, f"{key} is {', '.join(actual)}; this rule allows {' or '.join(allowed)} ({rule.summary})")
            if "min_evidence" in checks:
                cited = len(note.cited_evidence)
                if cited < checks["min_evidence"]:
                    report(1, f"cites {cited} of {checks['min_evidence']} required evidence sources (E-NNN) ({rule.summary})")
            if "max_age_days" in checks:
                updated = _parse_date(front.get("last_updated"))
                if updated is not None:
                    age = (today - updated).days
                    if age > checks["max_age_days"]:
                        report(1, f"last_updated is {age} days old; this rule allows {checks['max_age_days']} ({rule.summary})")
            needs_body = bool(rule.required or rule.forbidden or "required_sections" in checks)
            if not needs_body:
                continue
            if lines is None:
                lines = _body_lines(note)
            if "required_sections" in checks:
                if headings is None:
                    headings = []
                    for _, text in lines:
                        match = HEADING_RE.match(text)
                        if match:
                            headings.append((len(match.group(1)), match.group(2).strip().casefold()))
                for section in checks["required_sections"]:
                    level, title = _parse_section(section)
                    if not any(title == found and (level is None or level == depth) for depth, found in headings):
                        report(None, f"missing required section \"{section.strip()}\" ({rule.summary})")
            if not (rule.required or rule.forbidden):
                continue
            remaining_required = dict(rule.required)
            forbidden_hits: dict[str, int] = {}
            for number, text in lines:
                if len(text) > MAX_LINE_CHARS:
                    report(number, f"line is longer than {MAX_LINE_CHARS} characters; pattern checks read only its first {MAX_LINE_CHARS} ({rule.summary})")
                    text = text[:MAX_LINE_CHARS]
                if remaining_required:
                    for source, compiled in list(remaining_required.items()):
                        if compiled.search(text):
                            del remaining_required[source]
                for source, compiled in rule.forbidden:
                    if source not in forbidden_hits and compiled.search(text):
                        forbidden_hits[source] = number
            for source in remaining_required:
                report(None, f"no line matches required pattern /{source}/ ({rule.summary})")
            for source, number in forbidden_hits.items():
                report(number, f"matches forbidden pattern /{source}/ ({rule.summary})")
