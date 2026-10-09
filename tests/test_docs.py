"""Documentation must agree with the CLI it describes.

A quickstart that fails on its second command loses the reader for good, and a
flag that only exists in the docs is a bug report waiting to happen. These tests
parse every documented `whykit` command against the real argument parser, run
the README quickstart end to end, and check that relative links resolve.

They also hold every other name the docs promise to its source of truth: flags
(including inline code), exit codes, error codes, lint rule codes,
``whykit.toml`` keys, the command reference in ``docs/cli.md``, the generated
rule table, and the MCP server's tools, resources and prompts. The tutorials in
``docs/tutorials/`` are executed by ``tests/test_tutorials.py``.
"""
from __future__ import annotations

import argparse
import ast
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

from whykit import config as whykit_config  # noqa: E402
from whykit import mcp_server  # noqa: E402
from whykit import rule_policy  # noqa: E402
from whykit.cli import build_parser  # noqa: E402
from whykit.contract import ERROR_CODES, ERROR_EXIT_CODES  # noqa: E402
from whykit.rules import RULE_BY_CODE  # noqa: E402

DOC_FILES = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "scripts" / "README.md",
    ROOT / ".github" / "RELEASE.md",
    ROOT / "apps" / "explorer" / "README.md",
    *sorted((ROOT / "docs").rglob("*.md")),
    *sorted((ROOT / "examples").glob("*/README.md")),
    *sorted((ROOT / "src" / "whykit" / "template").rglob("*.md")),
)
DOCS = ROOT / "docs"
SHELL_FENCE = re.compile(r"```(?:bash|sh|shell)\n(.*?)```", re.S)
WHYKIT_CALL = re.compile(r"(?:^|&&\s*|\buv run (?:--project \S+ )?|\bpython3? scripts/whykit\.py )whykit (.*)|\bpython3? scripts/whykit\.py (.*)")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
CODE_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
# 0 and 1 are results; every other code is an error exit from the contract.
EXIT_CODES = {0, 1, 130} | set(ERROR_EXIT_CODES.values())
# Flags of other tools that the docs mention on their own, outside a command.
FOREIGN_FLAGS = {
    "--no-verify": "git commit: skipping hooks is what the docs warn against",
    "--locked": "uv sync / uv run",
    "--extra": "uv sync --extra mcp",
    "--no-dev": "uv sync in CI",
    "--only-group": "uv run --only-group dist",
}
FOREIGN_TOOLS = ("uv ", "git ", "npm ", "npx ", "pip ", "pipx ", "python", "gh ", "twine ", "zizmor ")
# Shell and argparse messages quoted in troubleshooting headings that WhyKit's
# own source does not contain.
FOREIGN_MESSAGES = {"whykit: command not found", "error: unrecognized arguments: --root …"}


def _prose(text: str) -> str:
    """Markdown with fenced blocks removed: what a reader sees as running text."""
    return re.sub(r"(?ms)^```.*?^```[ \t]*$", "", text)


def _code_spans(text: str) -> list[str]:
    return [span.replace("\\|", "|") for span in CODE_SPAN.findall(_prose(text))]


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {o for a in parser._actions for o in a.option_strings if o.startswith("--") and o != "--help"}


def _leaf_commands(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> dict[str, argparse.ArgumentParser]:
    found: dict[str, argparse.ArgumentParser] = {}
    for name, sub in _subparsers(parser).items():
        children = _leaf_commands(sub, (*prefix, name))
        found.update(children or {" ".join((*prefix, name)): sub})
    return found


def _all_cli_flags() -> set[str]:
    flags = _options(build_parser()) | _options(mcp_server.build_parser())
    for sub in _leaf_commands(build_parser()).values():
        flags |= _options(sub)
    for sub in _subparsers(build_parser()).values():
        flags |= _options(sub)
    return flags


def _resolve(tokens: list[str]) -> tuple[list[str], set[str]]:
    """Command path named by leading tokens, and the options valid there."""
    parser = build_parser()
    path: list[str] = []
    valid = _options(parser)
    # `whykit --root DIR <command>` hands --root to the command.
    if tokens[:1] == ["--root"]:
        tokens = tokens[2:]
    elif tokens and tokens[0].startswith("--root="):
        tokens = tokens[1:]
    for token in tokens:
        subs = _subparsers(parser)
        if not path and token.startswith("--") and token in valid:
            continue  # a global option before the command, such as `--no-cache`
        if token in subs:
            parser = subs[token]
            path.append(token)
            valid |= _options(parser)
            continue
        if subs and re.fullmatch(r"[a-z][a-z-]*", token):
            raise AssertionError(f"unknown command {' '.join([*path, token])!r}")
        break
    return path, valid


def _mcp_definitions() -> dict[str, dict[str, dict[str, object]]]:
    """Tools, prompts and resources the MCP server registers, read from its source.

    Read with ``ast`` so the check runs without the optional SDK installed.
    """
    tree = ast.parse((ROOT / "src" / "whykit" / "mcp_server.py").read_text(encoding="utf-8"))
    found: dict[str, dict[str, dict[str, object]]] = {"tool": {}, "prompt": {}, "resource": {}}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "mcp"):
                continue
            kind = decorator.func.attr
            if kind == "resource":
                uri = ast.literal_eval(decorator.args[0])
                found["resource"][uri] = {}
            elif kind in {"tool", "prompt"}:
                args = node.args.args
                defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
                params: dict[str, object] = {}
                for arg, default in zip(args, defaults, strict=True):
                    if default is None:
                        params[arg.arg] = "required"
                    else:
                        try:
                            params[arg.arg] = ast.literal_eval(default)
                        except ValueError:
                            params[arg.arg] = "<expr>"
                found[kind][node.name] = params
    return found


def _section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^(#+) {re.escape(heading)}\n(.*?)(?=^#{{1,3}} |\Z)", text)
    assert match is not None, heading
    return match.group(2)


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|\s*-", line):
            rows.append([cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))])
    return rows[1:] if rows else []


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
            command = re.split(r"\s+(?:&&|\||>|#)\s*", match.group(1) or match.group(2))[0]
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


class InlineReferenceTests(unittest.TestCase):
    """Names in running text and inline code, not only in shell blocks."""

    def test_inline_whykit_commands_name_real_commands_and_options(self) -> None:
        checked = 0
        for path in DOC_FILES:
            for span in _code_spans(path.read_text(encoding="utf-8")):
                if not span.startswith("whykit "):
                    continue
                try:
                    tokens = shlex.split(span)[1:]
                except ValueError:
                    tokens = span.split()[1:]
                if not tokens or re.fullmatch(r"…|\.\.\.|<[^>]+>", tokens[0]):
                    continue  # a placeholder such as `whykit …` or `whykit <command> --help`
                with self.subTest(doc=path.relative_to(ROOT).as_posix(), span=span):
                    path_, valid = _resolve(tokens)
                    self.assertTrue(path_ or tokens[0] in {"--version", "--help", "-V", "-h"}, span)
                    for token in tokens:
                        flag = token.split("=", 1)[0]
                        if flag.startswith("--") and flag != "--help":
                            self.assertIn(flag, valid, f"{flag} is not an option of `whykit {' '.join(path_)}`")
                    checked += 1
        self.assertGreater(checked, 60, "the inline extractor stopped finding commands")

    def test_every_flag_mentioned_in_prose_exists(self) -> None:
        known = _all_cli_flags() | set(FOREIGN_FLAGS)
        # The repository's own helper scripts are documented too.
        for script in (*sorted((ROOT / "scripts").glob("*.py")), ROOT / "tests" / "synthetic_vault.py"):
            known |= set(re.findall(r'add_argument\(\s*"(--[a-z][a-z0-9-]*)"', script.read_text(encoding="utf-8")))
        for path in DOC_FILES:
            for span in _code_spans(path.read_text(encoding="utf-8")):
                if span.startswith(FOREIGN_TOOLS) or span.startswith("whykit "):
                    continue  # whykit commands are checked against their own command above
                for flag in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", span):
                    with self.subTest(doc=path.relative_to(ROOT).as_posix(), span=span):
                        self.assertTrue(flag in known, f"{flag} is not an option of whykit, whykit-mcp or a repository script")

    def test_messages_in_the_source_name_real_commands(self) -> None:
        commands = set(_subparsers(build_parser()))
        for source in sorted((ROOT / "src" / "whykit").glob("*.py")):
            for name in re.findall(r"`whykit ([a-z][a-z-]*)", source.read_text(encoding="utf-8")):
                with self.subTest(source=source.name, command=name):
                    self.assertIn(name, commands)

    def test_exit_codes_mentioned_are_real(self) -> None:
        phrase = re.compile(r"\bexit(?:s|ed)?(?: with)?(?: (?:code|status))? `?(\d+)`?")
        for path in DOC_FILES:
            text = _prose(path.read_text(encoding="utf-8"))
            mentioned = {int(code) for code in phrase.findall(text)}
            for table in re.findall(r"(?m)^\| Code \|.*\n\|[- |]+\|\n((?:\|.*\n)+)", text):
                mentioned |= {int(code) for code in re.findall(r"(?m)^\| `(\d+)` \|", table)}
            with self.subTest(doc=path.relative_to(ROOT).as_posix()):
                self.assertLessEqual(mentioned, EXIT_CODES)

    def test_exit_code_tables_list_every_code(self) -> None:
        for name in ("automation.md", "cli.md"):
            text = (DOCS / name).read_text(encoding="utf-8")
            table = _section(text, "Exit codes")
            with self.subTest(doc=name):
                self.assertEqual({int(c) for c in re.findall(r"(?m)^\| `(\d+)` \|", table)}, EXIT_CODES)

    def test_error_code_table_matches_the_contract(self) -> None:
        table = _section((DOCS / "automation.md").read_text(encoding="utf-8"), "Error codes")
        documented = {code: int(exit_code) for code, exit_code in re.findall(r"(?m)^\| `([a-z_]+)` \| (\d+) \|", table)}
        self.assertEqual(documented, ERROR_EXIT_CODES)

    def test_error_codes_mentioned_anywhere_exist(self) -> None:
        mcp_codes = set(re.findall(r'ToolFailure\(\s*"([a-z_]+)"', (ROOT / "src" / "whykit" / "mcp_server.py").read_text(encoding="utf-8")))
        known = set(ERROR_CODES) | mcp_codes
        for path in DOC_FILES:
            text = path.read_text(encoding="utf-8")
            mentioned = set(re.findall(r'"code":\s*"([a-z_]+)"', text))
            mentioned |= set(re.findall(r"error code `([a-z_]+)`", _prose(text)))
            mentioned |= set(re.findall(r"`([a-z_]+)` error", _prose(text)))
            with self.subTest(doc=path.relative_to(ROOT).as_posix()):
                self.assertLessEqual(mentioned, known)

    def test_lint_rule_codes_mentioned_anywhere_exist(self) -> None:
        prefixes = {code.split(".")[0] for code in RULE_BY_CODE}
        for path in DOC_FILES:
            for span in _code_spans(path.read_text(encoding="utf-8")):
                match = re.fullmatch(r"([a-z_]+)\.([a-z0-9_]+)", span)
                if match and match.group(1) in prefixes:
                    with self.subTest(doc=path.relative_to(ROOT).as_posix(), code=span):
                        if span.endswith("_"):  # a documented prefix such as `decision.id_`
                            self.assertTrue(any(code.startswith(span) for code in RULE_BY_CODE), span)
                        else:
                            self.assertIn(span, RULE_BY_CODE)

    def test_troubleshooting_headings_quote_real_messages(self) -> None:
        text = (DOCS / "troubleshooting.md").read_text(encoding="utf-8")
        source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "whykit").glob("*.py"))
        source += (ROOT / "action.yml").read_text(encoding="utf-8")
        for heading in re.findall(r"(?m)^### (.+)$", text):
            for span in CODE_SPAN.findall(heading):
                if " " not in span or span.startswith(("whykit ", *FOREIGN_TOOLS)) or span in FOREIGN_MESSAGES:
                    continue
                for fragment in filter(None, (part.strip() for part in span.split("…"))):
                    with self.subTest(heading=heading, fragment=fragment):
                        self.assertIn(fragment, source)


class ReferencePageTests(unittest.TestCase):
    def test_command_reference_lists_every_command_with_exactly_its_options(self) -> None:
        table = _section((DOCS / "cli.md").read_text(encoding="utf-8"), "Commands")
        documented: dict[str, set[str]] = {}
        for row in _table_rows(table):
            command = re.sub(r"\s*[<\[].*$", "", row[0].strip("`"))
            documented[command] = set(re.findall(r"`(--[a-z-]+)`", row[2]))
        leaves = _leaf_commands(build_parser())
        self.assertEqual(set(documented), set(leaves))
        for command, parser in leaves.items():
            with self.subTest(command=command):
                self.assertEqual(documented[command], _options(parser))

    def test_command_reference_names_the_mcp_server_options(self) -> None:
        section = _section((DOCS / "cli.md").read_text(encoding="utf-8"), "`whykit-mcp`")
        for option in _options(mcp_server.build_parser()):
            self.assertIn(option, section)

    def test_rule_table_is_the_generated_catalog(self) -> None:
        doc = (DOCS / "rules.md").read_text(encoding="utf-8")
        table = re.search(r"<!-- rules:start -->\n(.*)<!-- rules:end -->", doc, re.S)
        self.assertIsNotNone(table)
        generated = subprocess.run([sys.executable, str(CLI), "rules", "--markdown"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertEqual(table.group(1).strip(), generated.stdout.strip())

    def test_configuration_reference_covers_every_policy_key(self) -> None:
        doc = (DOCS / "configuration.md").read_text(encoding="utf-8")
        for key in whykit_config._DEFAULT_KEYS:
            self.assertIn(f"`defaults.{key}`", doc)
        for key in whykit_config._PROFILE_KEYS:
            self.assertRegex(doc, rf"`(?:profiles\.<name>)?\.{key}`")
        for key in whykit_config._TOP_LEVEL_KEYS:
            self.assertIn(key, doc)

    def test_configuration_reference_covers_every_team_rule_key(self) -> None:
        doc = (DOCS / "configuration.md").read_text(encoding="utf-8")

        def keys(section: str) -> set[str]:
            return {key for row in _table_rows(_section(doc, section)) for key in re.findall(r"`([a-z_.]+)`", row[0])}

        expected = (rule_policy.CUSTOM_KEYS - {"applies_to"}) | {f"applies_to.{key}" for key in rule_policy.APPLIES_TO_KEYS}
        self.assertEqual(keys("Custom rules"), set(expected))
        self.assertEqual(keys("Overrides"), set(rule_policy.OVERRIDE_KEYS))

    def test_every_repository_script_is_documented(self) -> None:
        readme = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        scripts = [
            path for path in sorted((ROOT / "scripts").iterdir())
            # Only real script files: not README.md, bytecode caches, editor
            # or OS dotfiles, or any other directory a local run leaves behind.
            if path.is_file() and path.name != "README.md" and not path.name.startswith(".")
            and path.suffix not in {".pyc", ".pyo"}
        ]
        self.assertIn("whykit.py", [path.name for path in scripts])
        for script in scripts:
            with self.subTest(script=script.name):
                self.assertRegex(readme, rf"`(?:scripts/)?{re.escape(script.name)}`")
        releasing = (DOCS / "releasing.md").read_text(encoding="utf-8")
        self.assertIn("scripts/release_rehearsal.py", releasing)

    def test_policy_keys_mentioned_anywhere_exist(self) -> None:
        for path in DOC_FILES:
            text = _prose(path.read_text(encoding="utf-8"))
            with self.subTest(doc=path.relative_to(ROOT).as_posix()):
                for key in re.findall(r"`defaults\.([a-z_]+)`", text):
                    self.assertIn(key, whykit_config._DEFAULT_KEYS)
                for key in re.findall(r"`profiles\.(?:<name>|[a-z]+)\.([a-z_]+)`", text):
                    self.assertIn(key, whykit_config._PROFILE_KEYS)

    def test_docs_index_links_every_page(self) -> None:
        index = (DOCS / "README.md").read_text(encoding="utf-8")
        linked = {(DOCS / target.partition("#")[0]).resolve() for target in MD_LINK.findall(index)}
        for page in DOCS.rglob("*.md"):
            if page.parent.name == "media" or page.name == "README.md":
                continue
            with self.subTest(page=page.relative_to(ROOT).as_posix()):
                self.assertIn(page.resolve(), linked)
        for heading in ("Start", "Concepts", "How-to guides", "Reference", "Operations"):
            self.assertRegex(index, rf"(?m)^## {heading}$")


class MCPDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = (DOCS / "mcp.md").read_text(encoding="utf-8")
        cls.defined = _mcp_definitions()

    def test_tool_names_match_the_server(self) -> None:
        tools = self.defined["tool"]
        self.assertEqual(set(tools), set(mcp_server.TOOL_NAMES))
        returns = {row[0].strip("`") for row in _table_rows(_section(self.doc, "What each tool returns"))}
        self.assertEqual(returns, set(tools))
        intro = self.doc.split("\n## ", 1)[0]
        for name in tools:
            self.assertIn(f"`{name}`", intro)

    def test_input_schema_table_matches_every_tool_signature(self) -> None:
        documented: dict[str, dict[str, str]] = {}
        tool = ""
        for row in _table_rows(_section(self.doc, "Input schemas")):
            tool = row[0].strip("`") or tool
            for argument in re.findall(r"`([a-z_]+)`", row[1]):
                documented.setdefault(tool, {})[argument] = row[3]
        for name, params in self.defined["tool"].items():
            with self.subTest(tool=name):
                self.assertEqual(set(documented.get(name, {})), set(params))
                for argument, default in params.items():
                    cell = documented[name][argument]
                    if default == "required":
                        self.assertIn("required", cell)
                    elif isinstance(default, bool):
                        self.assertIn(f"`{str(default).lower()}`", cell)
                    elif isinstance(default, int):
                        self.assertIn(f"`{default}`", cell.replace(",", ""))
                    elif isinstance(default, str):
                        self.assertIn(f'`"{default}"`', cell)
                    elif isinstance(default, list):
                        self.assertIn("`[]`", cell)

    def test_resources_and_prompts_match_the_server(self) -> None:
        resources = {row[0].strip("`") for row in _table_rows(_section(self.doc, "Resources"))}
        self.assertEqual(resources, set(self.defined["resource"]))
        prompts = {}
        for row in _table_rows(_section(self.doc, "Prompts")):
            prompts[row[0].strip("`")] = set(re.findall(r"`([a-z_]+)`", row[1]))
        self.assertEqual(set(prompts), set(self.defined["prompt"]))
        for name, params in self.defined["prompt"].items():
            self.assertEqual(prompts[name], set(params), name)

    def test_counts_in_prose_match_the_server(self) -> None:
        words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
        expected = {"tools": len(self.defined["tool"]), "resources": len(self.defined["resource"]),
                    "prompts": len(self.defined["prompt"])}
        for path in (ROOT / "README.md", DOCS / "README.md", DOCS / "mcp.md", DOCS / "cli.md", DOCS / "guide.md"):
            text = _prose(path.read_text(encoding="utf-8"))
            for word, kind in re.findall(r"\b(one|two|three|four|five|six|seven|eight|nine) (tools|resources|prompts)\b", text):
                with self.subTest(doc=path.relative_to(ROOT).as_posix(), claim=f"{word} {kind}"):
                    self.assertEqual(word, words[expected[kind]])

    def test_error_table_matches_the_server(self) -> None:
        source = (ROOT / "src" / "whykit" / "mcp_server.py").read_text(encoding="utf-8")
        # `not_found` is raised only by prompts, which report it as the MCP
        # protocol error -32602 (documented under Prompts), never as a tool result.
        raised = set(re.findall(r'ToolFailure\(\s*"([a-z_]+)"', source)) - {"not_found"}
        self.assertIn("-32602", _section(self.doc, "Prompts"))
        documented = {row[0].strip("`") for row in _table_rows(_section(self.doc, "Errors"))}
        self.assertEqual(documented, raised)

    def test_server_options_are_documented(self) -> None:
        for option in _options(mcp_server.build_parser()):
            self.assertIn(option, self.doc)


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
                                        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, f"{line}\n{result.stdout}{result.stderr}")
                outputs.append(result.stdout)
            combined = "".join(outputs)
            self.assertIn("created E-001: 00-context/evidence-register.md", combined)
            self.assertIn("created D-001: 06-decisions/d-001-ship-sso-before-audit-logs.md", combined)
            # The separator is an em dash on UTF-8 consoles and "-" or a code-page byte elsewhere.
            self.assertRegex(combined, r"\d+ files \S 0 error\(s\), 5 warning\(s\)")
            # The fifth warning is the scaffold's prompts in the new decision.
            self.assertIn("[decision.placeholder]", combined)
            self.assertIn("0 review(s) overdue or due by", combined)


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
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertTrue((vault / ".whykit" / "mutation.lock").exists())
            subprocess.run(["git", "init", "-q", str(vault)], check=True, capture_output=True)
            tracked = subprocess.run(["git", "-C", str(vault), "status", "--porcelain", "--untracked-files=all"],
                                     check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            self.assertNotIn(".whykit/", tracked)
            self.assertIn("06-decisions/d-001-example.md", tracked)


if __name__ == "__main__":
    unittest.main()
