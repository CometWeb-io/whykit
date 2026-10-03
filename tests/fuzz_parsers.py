"""Seeded, budgeted fuzz harness for WhyKit's hand-written parsers.

Standard library only. Every case is generated from ``random.Random(seed)``,
so a failure prints the seed, target and case number needed to replay it::

    python tests/fuzz_parsers.py --seed 20261003 --cases 400
    python tests/fuzz_parsers.py --target front_matter --seed 7 --cases 5000

Each target has an oracle: the exceptions a parser may raise on bad input
(``ValueError`` from the front matter reader, ``PolicyError`` from a rule
table, ``CalledProcessError`` from Git output parsing) and the properties its
result must keep (a resolved link stays inside the vault, a safe diff path
never contains ``..``). Anything else is a crash.

Every case also runs under two budgets: wall time (a POSIX interval timer
interrupts a hang, which the regular expression engine honours) and peak
traced memory. ``scaling`` targets feed the same pathological input at two
sizes and fail when the larger one costs disproportionately more, which is how
quadratic regular expressions show up long before they hang a CI job.

``tests/test_fuzz_parsers.py`` runs a small fixed-seed round on every test run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import shutil
import signal
import string
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whykit import lint as lint_mod  # noqa: E402
from whykit.diff import _safe_parts  # noqa: E402
from whykit.immutability import _diff_entries  # noqa: E402
from whykit.lint import (  # noqa: E402
    Note,
    _build_index,
    _mask_code,
    _parse_front_matter,
    _resolve,
    _split_table_row,
    check_markdown_links,
    check_wikilinks,
    load_note,
)
from whykit.rule_policy import (  # noqa: E402
    PolicyError,
    build_policy,
    check_custom_rules,
    regex_problem,
)

DEFAULT_SEED = 20261003
CASE_SECONDS = 2.0
CASE_PEAK_BYTES = 64 * 1024 * 1024
TODAY = dt.date(2026, 10, 3)

# Characters that are syntax somewhere in front matter, Markdown, tables,
# links, globs or regular expressions, plus whitespace, controls, bidi and
# zero-width code points, confusable digits, a lone surrogate and an astral
# code point.
SYNTAX = list("-:#[]{}()|\\/'\"`~*!>?.,&@%^$+= \t\n\r")
UNICODE = ["\u00e9", "\u4e2d", "\u202e", "\u2066", "\u200b", "\ufeff", "\u2028", "\uff11", "\u0430", "\x00", "\x7f", "\ud800", "\U0001F600"]
ALPHABET = list(string.ascii_letters + string.digits) + SYNTAX * 2 + UNICODE


class Hang(Exception):
    """Raised by the watchdog when a case exceeds its time budget."""


@dataclass
class Failure:
    target: str
    seed: int
    case: int
    kind: str
    detail: str
    sample: str

    def __str__(self) -> str:
        replay = (
            "python tests/fuzz_parsers.py --target none" if self.target.startswith("scaling:")
            else f"python tests/fuzz_parsers.py --target {self.target} --seed {self.seed} --cases {self.case + 1} --no-scaling"
        )
        return (
            f"[{self.kind}] target={self.target} seed={self.seed} case={self.case}: {self.detail}\n"
            f"    replay: {replay}\n"
            f"    input: {self.sample[:300]!r}"
        )


def _text(rng: random.Random, low: int = 0, high: int = 24) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(low, high)))


def _word(rng: random.Random) -> str:
    return rng.choice(["title", "type", "status", "owner", "sensitivity", "aliases", "source_ids", "tags",
                       "provenance", "review_by", "decision_id", "supersedes", "Sensitivity", "__proto__", ""])


# ---------------------------------------------------------------------------
# Generators


def gen_front_matter(rng: random.Random) -> str:
    lines: list[str] = []
    for _ in range(rng.randint(0, 12)):
        shape = rng.randrange(12)
        indent = " " * rng.choice([0, 0, 0, 1, 2, 4])
        if shape == 0:
            lines.append(f"{indent}{_word(rng)}: {_text(rng)}")
        elif shape == 1:
            lines.append(f"{indent}- {_text(rng)}")
        elif shape == 2:
            depth = rng.randint(1, 3000)
            lines.append(f"{_word(rng)}: " + "[" * depth + _text(rng, 0, 4) + "]" * rng.randint(0, depth))
        elif shape == 3:
            lines.append(f"{_word(rng)}: \"{_text(rng)}\\u{rng.choice(['d800', 'dfff', '0000', '202e', 'zzzz'])}\"")
        elif shape == 4:
            lines.append(f"{_word(rng)}: {rng.choice(['|', '>-', '|+2', '&a', '*a', '!!str', '{a: 1}'])}")
        elif shape == 5:
            lines.append(f"{indent}{_text(rng)}")
        elif shape == 6:
            lines.append(f"\t{_word(rng)}: x")
        elif shape == 7:
            lines.append(f"{_word(rng)}: '" + "''" * rng.randint(0, 50) + "'")
        elif shape == 8:
            lines.append(f"{_word(rng)}: " + ", ".join(_text(rng, 0, 4) for _ in range(rng.randint(0, 2000))))
        elif shape == 9:
            lines.append("#" + _text(rng))
        elif shape == 10:
            lines.append(f"{indent}{_text(rng, 1, 6)}: " + "\"" * rng.randint(0, 5))
        else:
            lines.append("")
    return "\n".join(lines)


def gen_note(rng: random.Random) -> str:
    head = "---\n" + gen_front_matter(rng) + rng.choice(["\n---\n", "\n---", "\n", "\n--- \n"])
    if rng.random() < 0.2:
        head = rng.choice(["", "\ufeff", "---", "---\r\n", "\r"]) + head
    body = []
    for _ in range(rng.randint(0, 20)):
        shape = rng.randrange(8)
        if shape == 0:
            body.append("```" + _text(rng) + "\n" + _text(rng))
        elif shape == 1:
            body.append(f"[[{_text(rng)}]]")
        elif shape == 2:
            body.append(f"[{_text(rng, 0, 6)}]({_text(rng)})")
        elif shape == 3:
            body.append("| " + " | ".join(_text(rng, 0, 8) for _ in range(rng.randint(1, 8))) + " |")
        elif shape == 4:
            body.append(rng.choice(["E-", "D-", "E-\uff10\uff10\uff11", "E-" + "9" * rng.randint(1, 5000)]))
        else:
            body.append(_text(rng, 0, 80))
    return head + "\n".join(body)


def gen_table_row(rng: random.Random) -> str:
    units = ["|", "\\|", "[[", "]]", "[[a|b]]", " ", "x", "\\", "`", "\u202e"]
    return "".join(rng.choice(units) for _ in range(rng.randint(0, 400)))


def gen_link_target(rng: random.Random) -> str:
    shape = rng.randrange(10)
    if shape == 0:
        return "../" * rng.randint(1, 4) + _text(rng, 1, 8)
    if shape == 1:
        return rng.choice(["/etc/passwd", "C:\\Windows\\win.ini", "\\\\server\\share\\x", "~/x", "file:///etc/passwd"])
    if shape == 2:
        return "a" * rng.randint(200, 5000)
    if shape == 3:
        return _text(rng, 1, 8) + "\x00" + _text(rng, 0, 4)
    if shape == 4:
        return "/".join("a" * rng.randint(1, 300) for _ in range(rng.randint(1, 30)))
    if shape == 5:
        return "%2e%2e/" * rng.randint(1, 3) + "x"
    if shape == 6:
        return "notes/\u202eexe.md" + rng.choice(["", "#h", "|alias"])
    return _text(rng, 1, 30)


def gen_pattern(rng: random.Random) -> str:
    atoms = ["a", "b", ".", "\\s", "\\S", "\\w", "\\d", "[ab]", "[^a]", "x", " ", "(?:a)", "(a)", "(?i)", "(?x)",
             "(?#c)", "(?=a)", "(?!b)", "^", "$", "\\b", "|", "(", ")", "(?:", "(?P<n>"]
    quants = ["", "", "*", "+", "?", "{2}", "{1,3}", "{2,}", "{,9}", "*?", "+?", "{30}"]
    out = "".join(rng.choice(atoms) + rng.choice(quants) for _ in range(rng.randint(1, 8)))
    return out[:256]


def gen_rule_table(rng: random.Random) -> object:
    def value() -> object:
        return rng.choice([
            _text(rng, 0, 10), rng.randint(-5, 200_000), True, None, [], [_text(rng, 0, 6)],
            {"a": 1}, [gen_pattern(rng) for _ in range(rng.randint(0, 3))], 1.5,
            "*" * rng.randint(0, 600), "../x/**", "/abs/**", "07-research/**",
        ])
    custom = []
    for _ in range(rng.randint(0, 3)):
        rule: dict[str, object] = {"id": rng.choice([f"custom.r{rng.randint(0, 9)}", _text(rng, 0, 8), 1])}
        for key in rng.sample(["level", "summary", "why", "fix", "applies_to", "required_keys", "required_values",
                               "required_sections", "required_patterns", "forbidden_patterns", "min_evidence",
                               "max_age_days", "bogus"], rng.randint(0, 5)):
            if key in ("required_patterns", "forbidden_patterns"):
                rule[key] = [gen_pattern(rng) for _ in range(rng.randint(0, 4))]
            elif key == "applies_to":
                rule[key] = {rng.choice(["type", "status", "paths", "workstream", "x"]): value()}
            elif key == "required_values":
                rule[key] = {_word(rng) or "k": value()}
            elif key == "level":
                rule[key] = rng.choice(["error", "warning", "off", "info", 3])
            else:
                rule[key] = value()
        custom.append(rule)
    table: dict[str, object] = {"custom": custom}
    if rng.random() < 0.5:
        table["overrides"] = {rng.choice(["note.orphan", "secret.detected", "custom.r1", _text(rng, 0, 8)]): rng.choice([
            {"level": rng.choice(["off", "warning", "error", "x"]), "paths": value(), "reason": value()},
            [{"level": "off", "paths": ["**"]}], "off", None])}
    if rng.random() < 0.05:
        return rng.choice([None, [], "x", {"bogus": 1}])
    return table


# Accepted policy patterns may still be quadratic in the line length (a
# leading `\w+` searched from every position is), which the per-run time
# budget bounds. At this length a quadratic pattern finishes in well under the
# case budget while anything cubic or exponential does not, so a hang here
# means the pattern check let a worse shape through.
FUZZ_LINE_CHARS = 2_000


def adversarial_lines(pattern: str, rng: random.Random, length: int = FUZZ_LINE_CHARS) -> list[str]:
    """Lines built from the pattern's own characters, the classic ReDoS food."""
    chars = [c for c in pattern if c.isalnum() or c in " \t"] or ["a"]
    lines = []
    for unit in (chars, ["a"], [" "], ["a", "b"], ["x", " "]):
        body = "".join(unit[i % len(unit)] for i in range(length))
        lines.append(body)
        lines.append(body[: length - 1] + "!")
    rng.shuffle(lines)
    return lines


# ---------------------------------------------------------------------------
# Targets: each takes (rng, workdir) and returns the input sample it used.

_LINK_ROOT_CACHE: dict[str, Path] = {}


def _link_root(workdir: Path) -> Path:
    root = workdir / "linkvault"
    if not root.exists():
        (root / "notes").mkdir(parents=True)
        (root / "Home.md").write_text("---\ntitle: Home\n---\n", encoding="utf-8")
        (root / "00-context").mkdir()
        (root / "notes" / "a.md").write_text("---\ntitle: a\naliases: [b]\n---\n", encoding="utf-8")
        (workdir / "outside.md").write_text("outside\n", encoding="utf-8")
        try:
            (root / "notes" / "escape").symlink_to(workdir, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
    return root.resolve()


def target_front_matter(rng: random.Random, workdir: Path) -> str:
    raw = gen_front_matter(rng)
    try:
        result = _parse_front_matter(raw)
    except ValueError:
        return raw
    assert isinstance(result, dict), type(result)
    return raw


def target_note(rng: random.Random, workdir: Path) -> str:
    text = gen_note(rng)
    note = load_note(Path("note.md"), text=text)
    assert isinstance(note.front, dict)
    _ = note.body, note.masked, note.cited_evidence
    assert len(note.masked) == len(note.text)
    return text


def target_table_row(rng: random.Random, workdir: Path) -> str:
    row = gen_table_row(rng)
    cells = _split_table_row(row)
    assert cells and all(isinstance(cell, str) for cell in cells)
    return row


def target_links(rng: random.Random, workdir: Path) -> str:
    root = _link_root(workdir)
    targets = [gen_link_target(rng) for _ in range(rng.randint(1, 6))]
    text = "\n".join(rng.choice(["[[{}]]", "![[{}]]", "[x]({})", "![i](<{}>)"]).format(t) for t in targets)
    note = Note(path=root / "notes" / "a.md", text=text)
    notes = [note, load_note(root / "Home.md")]
    index = _build_index(notes)
    findings: list = []
    with lint_mod.path_cache():
        check_wikilinks(root, note, index, findings)
        check_markdown_links(root, note, findings)
        for target in targets:
            resolved, _ = _resolve(root, target, index)
            if resolved is not None:
                assert lint_mod._within(root, resolved), f"resolved outside the vault: {resolved}"
    return text


def target_regex(rng: random.Random, workdir: Path) -> str:
    pattern = gen_pattern(rng)
    if regex_problem(pattern) is None:
        import re
        compiled = re.compile(pattern)
        for line in adversarial_lines(pattern, rng):
            compiled.search(line)
    return pattern


def target_rule_config(rng: random.Random, workdir: Path) -> str:
    table = gen_rule_table(rng)
    try:
        policy = build_policy(table)
    except PolicyError:
        return repr(table)
    note_text = "---\ntitle: t\ntype: decision\nstatus: approved\n---\n" + "\n".join(adversarial_lines(" ".join(
        p for rule in policy.custom for p, _ in (*rule.required, *rule.forbidden)) or "a", rng))
    note = load_note(Path("07-research/n.md"), text=note_text)
    check_custom_rules(policy, [note], [], TODAY, lambda path: path.as_posix())
    return repr(table)


def target_diff_paths(rng: random.Random, workdir: Path) -> str:
    tokens = []
    for _ in range(rng.randint(0, 10)):
        tokens.append(rng.choice(["M", "A", "D", "R100", "C75", "", "T", "R"]))
        tokens.extend(gen_link_target(rng) for _ in range(rng.randint(0, 3)))
    out = "\0".join(tokens) + rng.choice(["", "\0"])
    try:
        entries = _diff_entries(out)
    except subprocess.CalledProcessError:
        entries = []
    for status, old, new in entries:
        assert status and isinstance(old, str) and isinstance(new, str)
    for token in tokens:
        parts = _safe_parts(token)
        if parts is not None:
            assert not any(part in ("", ".", "..", ".git") for part in parts), parts
            base = (workdir / "dest").resolve()
            joined = base.joinpath(*parts)
            assert os.path.commonpath([base, joined]) == str(base) or os.path.isabs(token), token
    return out


def _pipeline_vault(workdir: Path) -> Path:
    template = workdir / "template-vault"
    if not template.exists():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _vaults import fresh_vault

        fresh_vault(template)
    vault = workdir / "pipeline-vault"
    shutil.rmtree(vault, ignore_errors=True)
    shutil.copytree(template, vault, symlinks=True)
    return vault.resolve()


def gen_register_row(rng: random.Random) -> str:
    cells = [rng.choice(["E-001", "E-002", "E-\uff10\uff10\uff13", "E-" + "9" * rng.randint(3, 40), _text(rng, 0, 4)])]
    cells += [_text(rng, 0, 12) for _ in range(rng.randint(0, 8))]
    return "| " + " | ".join(cells) + " |"


def target_vault(rng: random.Random, workdir: Path) -> str:
    """Hostile notes in a real vault, through every read-only command."""
    from whykit.backlinks import build_backlinks
    from whykit.context import build_context
    from whykit.evidence import list_evidence
    from whykit.explorer_index import build_explorer_index
    from whykit.graph import as_dot, as_mermaid, as_obsidian, build_graph
    from whykit.impact import analyze_impact
    from whykit.lint import lint
    from whykit.pack import build_pack
    from whykit.query import query_vault
    from whykit.status import build_status
    from whykit.trace import build_trace

    vault = _pipeline_vault(workdir)
    written = []
    for index in range(rng.randint(1, 4)):
        folder = rng.choice(["06-decisions", "07-research", "notes", "notes/deep/er"])
        name = rng.choice([f"d-00{index + 1}-x", f"n{index}", _text(rng, 1, 6).replace("/", "_").replace("\x00", "_")])
        path = vault / folder / f"{name}.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(gen_note(rng), encoding="utf-8", errors="surrogatepass")
        except (OSError, ValueError, UnicodeError):
            continue
        written.append(path)
    register = vault / "00-context" / "evidence-register.md"
    text = register.read_text(encoding="utf-8")
    rows = "\n".join(gen_register_row(rng) for _ in range(rng.randint(0, 6)))
    # surrogatepass: a lone surrogate becomes bytes that are not valid UTF-8,
    # which is what a damaged or hostile file on disk looks like.
    register.write_text(text.replace("## Retired sources", rows + "\n\n## Retired sources\n" + rows),
                        encoding="utf-8", errors="surrogatepass")
    sample = "\n---8<---\n".join(p.read_text(encoding="utf-8", errors="replace")[:400] for p in written)
    target = rng.choice([*(p.relative_to(vault).as_posix() for p in written), "E-001", "D-001", "Home", _text(rng, 1, 10)])
    with lint_mod.path_cache():
        lint(vault, today=TODAY)
        graph = build_graph(vault)
        as_dot(graph), as_mermaid(graph), as_obsidian(graph)
        build_status(vault, today=TODAY)
        build_explorer_index(vault, today=TODAY)
        build_trace(vault, today=TODAY)
        query_vault(vault, text=_text(rng, 0, 6) or None)
        list_evidence(vault)
        build_pack(vault, query=_text(rng, 1, 6))
        for call in (build_context, analyze_impact, build_backlinks):
            try:
                call(vault, target)
            except (ValueError, LookupError):
                pass
    return sample


TARGETS: dict[str, Callable[[random.Random, Path], str]] = {
    "front_matter": target_front_matter,
    "note": target_note,
    "table_row": target_table_row,
    "links": target_links,
    "regex": target_regex,
    "rule_config": target_rule_config,
    "diff_paths": target_diff_paths,
    "vault": target_vault,
}
# The whole-vault target runs every command per case; it gets fewer cases.
CASE_SHARE = {"vault": 0.1}
CASE_SECONDS_FOR = {"vault": 10.0}

# Pathological repetition, fed at two sizes. The parser must stay close to
# linear: four times the input may cost at most SCALING_LIMIT times as much.
SCALING_UNITS: dict[str, tuple[str, Callable[[str], object]]] = {
    "wikilink_openers": ("[", lambda s: list(lint_mod.WIKILINK_RE.finditer(s))),
    "wikilink_alias": ("[[a|", lambda s: list(lint_mod.WIKILINK_RE.finditer(s))),
    "wikilink_heading": ("[[a#", lambda s: list(lint_mod.WIKILINK_RE.finditer(s))),
    "markdown_link_open": ("[](", lambda s: list(lint_mod.MARKDOWN_LINK_RE.finditer(s))),
    "markdown_link_text": ("[a", lambda s: list(lint_mod.MARKDOWN_LINK_RE.finditer(s))),
    "fences": ("```\n~~~x\n", _mask_code),
    "inline_code": ("`a", _mask_code),
    "table_wikilinks": ("[[|", _split_table_row),
    "front_matter_list": ("[", lambda s: _safe(_parse_front_matter, "k: " + s + "]" * len(s))),
    "front_matter_quotes": ("'", lambda s: _safe(_parse_front_matter, "k: [" + s + "]")),
    "note": ("[[a|b](", lambda s: load_note(Path("n.md"), text="---\ntitle: x\n---\n" + s).cited_evidence),
}
SCALING_SIZE = 20_000
SCALING_LIMIT = 8.0
# Below this the timer measures interpreter noise, not the parser.
SCALING_FLOOR_SECONDS = 0.02
SCALING_REPORT_SECONDS = 0.2


def _safe(fn: Callable[[str], object], arg: str) -> object:
    try:
        return fn(arg)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Budgets


class _Watchdog:
    """Interrupt the main thread after *seconds* (POSIX); a no-op elsewhere."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.armed = hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()

    def __enter__(self) -> _Watchdog:
        if self.armed:
            self.previous = signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        self.started = time.perf_counter()
        return self

    def _fire(self, signum: int, frame: object) -> None:
        raise Hang(f"exceeded {self.seconds:.1f}s")

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.perf_counter() - self.started
        if self.armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous)


def _measure(fn: Callable[[], object], seconds: float) -> float:
    with _Watchdog(seconds) as watchdog:
        fn()
    return watchdog.elapsed


def run_target(name: str, seed: int, cases: int, workdir: Path, *, seconds: float = CASE_SECONDS,
               peak_bytes: int = CASE_PEAK_BYTES) -> list[Failure]:
    target = TARGETS[name]
    rng = random.Random(f"{name}:{seed}")
    failures: list[Failure] = []
    sample = ""
    for case in range(cases):
        state = rng.getstate()
        tracemalloc.start()
        try:
            with _Watchdog(seconds) as watchdog:
                sample = target(rng, workdir)
            _, peak = tracemalloc.get_traced_memory()
            if not watchdog.armed and watchdog.elapsed > seconds:
                raise Hang(f"took {watchdog.elapsed:.2f}s")
            if peak > peak_bytes:
                failures.append(Failure(name, seed, case, "memory", f"peak {peak // 1024} KiB", sample))
        except Hang as exc:
            failures.append(Failure(name, seed, case, "hang", str(exc), _replay_sample(name, state, workdir)))
        except Exception as exc:  # noqa: BLE001 - the harness reports every crash
            failures.append(Failure(name, seed, case, "crash", f"{type(exc).__name__}: {exc}"[:300],
                                    _replay_sample(name, state, workdir)))
        finally:
            tracemalloc.stop()
    return failures


def _replay_sample(name: str, state: object, workdir: Path) -> str:
    """Regenerate the failing input without running the parser on it."""
    rng = random.Random()
    rng.setstate(state)  # type: ignore[arg-type]
    generators = {
        "front_matter": gen_front_matter, "note": gen_note, "table_row": gen_table_row,
        "regex": gen_pattern, "rule_config": lambda r: repr(gen_rule_table(r)),
        "links": gen_link_target, "diff_paths": gen_link_target, "vault": gen_note,
    }
    try:
        return str(generators[name](rng))
    except Exception:  # noqa: BLE001
        return "<unavailable>"


def run_scaling(size: int = SCALING_SIZE, limit: float = SCALING_LIMIT, seconds: float = 10.0) -> list[Failure]:
    failures: list[Failure] = []
    for name, (unit, fn) in SCALING_UNITS.items():
        small = unit * (size // len(unit))
        large = unit * (4 * size // len(unit))
        try:
            base = max(min(_measure(lambda: fn(small), seconds) for _ in range(3)), SCALING_FLOOR_SECONDS)  # noqa: B023
            grown = min(_measure(lambda: fn(large), seconds) for _ in range(2))  # noqa: B023
        except Hang as exc:
            failures.append(Failure(f"scaling:{name}", 0, 0, "hang", str(exc), unit))
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append(Failure(f"scaling:{name}", 0, 0, "crash", f"{type(exc).__name__}: {exc}"[:300], unit))
            continue
        if grown / base > limit and grown > SCALING_REPORT_SECONDS:
            failures.append(Failure(f"scaling:{name}", 0, 0, "superlinear",
                                    f"{size} chars {base:.3f}s, {4 * size} chars {grown:.3f}s", unit))
    return failures


def run_all(seed: int = DEFAULT_SEED, cases: int = 200, targets: list[str] | None = None,
            scaling: bool = True) -> list[Failure]:
    workdir = Path(tempfile.mkdtemp(prefix="whykit-fuzz-"))
    try:
        failures: list[Failure] = []
        for name in [t for t in (targets or list(TARGETS)) if t != "none"]:
            share = max(1, int(cases * CASE_SHARE.get(name, 1.0)))
            if name == "vault":
                _pipeline_vault(workdir)  # the one-off `whykit init` is not part of any case
            failures.extend(run_target(name, seed, share, workdir, seconds=CASE_SECONDS_FOR.get(name, CASE_SECONDS)))
        if scaling:
            failures.extend(run_scaling())
        return failures
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--cases", type=int, default=300, help="cases per target")
    parser.add_argument("--target", action="append", choices=[*sorted(TARGETS), "none"],
                        help="repeatable; default all; `none` runs only the scaling checks")
    parser.add_argument("--no-scaling", action="store_true", help="skip the superlinear-time checks")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    failures = run_all(args.seed, args.cases, args.target, scaling=not args.no_scaling)
    for failure in failures:
        print(failure)
    print(f"{len(failures)} failure(s); seed {args.seed}, {args.cases} case(s) per target, "
          f"{time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
