"""Packaging invariants: what a wheel and sdist of WhyKit must and must not be.

Two layers. The first reads ``pyproject.toml`` and pins the metadata promises
(PEP 639 licensing, zero runtime dependencies, reproducible build backend,
anchored sdist allowlist). The second exercises ``scripts/check_dist.py`` on
small synthetic archives, so every rule the release gate enforces has a case
that passes and a case that must fail. CI runs the same script on the real
``uv build`` output, built twice, together with the SBOM.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]

_spec = importlib.util.spec_from_file_location("check_dist", ROOT / "scripts" / "check_dist.py")
assert _spec is not None and _spec.loader is not None
check_dist = importlib.util.module_from_spec(_spec)
sys.modules["check_dist"] = check_dist
_spec.loader.exec_module(check_dist)

VERSION = check_dist.package_version()
README_LINK = "https://github.com/CometWeb-io/whykit/blob/main/docs/guide.md"


def metadata(
    *,
    version: str = VERSION,
    classifiers: tuple[str, ...] = ("Programming Language :: Python :: 3",),
    requires: tuple[str, ...] = ("mcp<3,>=2; extra == 'mcp'",),
    license_expression: str | None = "Apache-2.0",
    body: str = f"# WhyKit\n\nSee the [guide]({README_LINK}) and [start](#start-here).\n",
) -> str:
    lines = ["Metadata-Version: 2.4", "Name: whykit", f"Version: {version}"]
    if license_expression:
        lines.append(f"License-Expression: {license_expression}")
    lines += ["License-File: LICENSE", "License-File: NOTICE"]
    lines += [f"Classifier: {c}" for c in classifiers]
    lines += ["Requires-Python: >=3.11", "Provides-Extra: mcp"]
    lines += [f"Requires-Dist: {r}" for r in requires]
    lines.append("Description-Content-Type: text/markdown")
    return "\n".join(lines) + "\n\n" + body


def write_wheel(directory: Path, *, version: str = VERSION, meta: str | None = None,
                extra: dict[str, str] | None = None, drop: tuple[str, ...] = ()) -> Path:
    dist_info = f"whykit-{version}.dist-info"
    files = {
        "whykit/__init__.py": f'__version__ = "{version}"\n',
        "whykit/py.typed": "",
        f"{dist_info}/METADATA": meta if meta is not None else metadata(version=version),
        f"{dist_info}/entry_points.txt": "[console_scripts]\nwhykit = whykit.cli:main\nwhykit-mcp = whykit.mcp_server:main\n",
        f"{dist_info}/licenses/LICENSE": "Apache License\n",
        f"{dist_info}/licenses/NOTICE": "WhyKit\n",
    }
    files.update(dict.fromkeys(check_dist.template_files(ROOT), ""))
    files.update(extra or {})
    for name in drop:
        files.pop(name)
    path = directory / f"whykit-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    return path


def write_sdist(directory: Path, *, version: str = VERSION, meta: str | None = None,
                extra: dict[str, str] | None = None, symlink: str | None = None) -> Path:
    prefix = f"whykit-{version}"
    files = {
        "PKG-INFO": meta if meta is not None else metadata(version=version),
        "pyproject.toml": "[project]\nname = 'whykit'\n",
        "README.md": "# WhyKit\n",
        "CHANGELOG.md": "# Changelog\n",
        "LICENSE": "Apache License\n",
        "NOTICE": "WhyKit\n",
        "src/whykit/__init__.py": f'__version__ = "{version}"\n',
        "src/whykit/py.typed": "",
        "schemas/graph.schema.json": "{}\n",
        "examples/tiny/Home.md": "# Home\n",
    }
    files.update(extra or {})
    path = directory / f"{prefix}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, content in sorted(files.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(f"{prefix}/{symlink}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
    return path


class PyprojectMetadataTests(unittest.TestCase):
    def test_license_is_a_pep_639_expression_without_legacy_classifiers(self) -> None:
        self.assertEqual(PROJECT["license"], "Apache-2.0")
        self.assertEqual(PROJECT["license-files"], ["LICENSE", "NOTICE"])
        self.assertFalse([c for c in PROJECT["classifiers"] if c.startswith("License ::")])

    def test_author_and_maintainer_are_the_natural_person_with_the_public_contact(self) -> None:
        person = {"name": "Maciej Zmitrukiewicz", "email": "hello@cometweb.io"}
        self.assertEqual(PROJECT["authors"], [person])
        self.assertEqual(PROJECT["maintainers"], [person])

    def test_python_classifiers_match_requires_python_and_the_ci_matrix(self) -> None:
        minimum = re.fullmatch(r">=3\.(\d+)", PROJECT["requires-python"])
        self.assertIsNotNone(minimum)
        assert minimum is not None
        versions = sorted(
            int(c.rsplit(".", 1)[1]) for c in PROJECT["classifiers"]
            if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)
        )
        self.assertEqual(versions[0], int(minimum.group(1)))
        self.assertEqual(versions, list(range(versions[0], versions[-1] + 1)))
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        matrix = re.search(r'python-version: \[([^\]]+)\]', ci)
        assert matrix is not None
        self.assertEqual([f"3.{v}" for v in versions], [v.strip(' "') for v in matrix.group(1).split(",")])

    def test_typed_package_is_declared_and_shipped(self) -> None:
        self.assertIn("Typing :: Typed", PROJECT["classifiers"])
        self.assertTrue((ROOT / "src" / "whykit" / "py.typed").is_file())

    def test_core_package_has_no_runtime_dependencies(self) -> None:
        self.assertEqual(PROJECT["dependencies"], [])
        self.assertEqual(set(PROJECT["optional-dependencies"]), check_dist.ALLOWED_EXTRAS)

    def test_project_urls_are_absolute_https_links_into_the_org_repository(self) -> None:
        for label in ("Homepage", "Source", "Changelog", "Issues", "Documentation"):
            self.assertIn(label, PROJECT["urls"])
        for label, url in PROJECT["urls"].items():
            with self.subTest(label=label):
                self.assertTrue(url.startswith("https://github.com/CometWeb-io/whykit"), url)

    def test_build_backend_and_plugins_are_pinned_exactly(self) -> None:
        requires = PYPROJECT["build-system"]["requires"]
        self.assertTrue(requires)
        for requirement in requires:
            with self.subTest(requirement=requirement):
                self.assertRegex(requirement, r"^[A-Za-z0-9_.-]+==\d+(\.\d+)*$")
        hatchling = next(r for r in requires if r.startswith("hatchling=="))
        major, minor = (int(p) for p in hatchling.split("==")[1].split(".")[:2])
        self.assertGreaterEqual((major, minor), (1, 27), "PEP 639 metadata needs hatchling >= 1.27")

    def test_readme_is_rendered_through_the_link_rewriting_hook(self) -> None:
        self.assertIn("readme", PROJECT["dynamic"])
        self.assertNotIn("readme", PROJECT)
        hook = PYPROJECT["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
        self.assertEqual(hook["content-type"], "text/markdown")
        self.assertEqual(hook["fragments"], [{"path": "README.md"}])

    def test_link_rewriting_leaves_no_relative_link_in_the_real_readme(self) -> None:
        hook = PYPROJECT["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(check_dist.RELATIVE_LINK.search(text), "README has no relative link left to test against")
        for substitution in hook["substitutions"]:
            text = re.sub(substitution["pattern"], substitution["replacement"], text)
        self.assertEqual(check_dist.RELATIVE_LINK.findall(re.sub(r"(?ms)^```.*?^```\s*", "", text)), [])
        self.assertIn("](https://raw.githubusercontent.com/CometWeb-io/whykit/main/docs/media/overview.svg)", text)

    def test_link_rewriting_keeps_anchors_and_absolute_urls(self) -> None:
        hook = PYPROJECT["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
        text = "[a](#start-here) [b](https://example.com/x) [c](mailto:a@example.com) ![d](img/x.svg) [e](docs/x.md#y)"
        for substitution in hook["substitutions"]:
            text = re.sub(substitution["pattern"], substitution["replacement"], text)
        self.assertEqual(
            text,
            "[a](#start-here) [b](https://example.com/x) [c](mailto:a@example.com) "
            "![d](https://raw.githubusercontent.com/CometWeb-io/whykit/main/img/x.svg) "
            "[e](https://github.com/CometWeb-io/whykit/blob/main/docs/x.md#y)",
        )

    def test_sdist_allowlist_is_anchored_to_the_project_root(self) -> None:
        include = PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
        for pattern in include:
            with self.subTest(pattern=pattern):
                self.assertTrue(pattern.startswith("/"), "unanchored patterns also match nested files")
                self.assertNotIn("tests", pattern)
        listed = {pattern.strip("/").split("/")[0] for pattern in include}
        self.assertLessEqual(listed, check_dist.SDIST_TOP_LEVEL)

    def test_sbom_tool_is_pinned_in_the_release_tooling_group(self) -> None:
        dist = PYPROJECT["dependency-groups"]["dist"]
        self.assertTrue(any(re.fullmatch(r"cyclonedx-bom==\d+(\.\d+)*", r) for r in dist), dist)
        self.assertTrue(any(re.fullmatch(r"twine==\d+(\.\d+)*", r) for r in dist), dist)


class CheckDistTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dist = self.tmp / "dist"
        self.dist.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_check(self, *args: str) -> tuple[int, str]:
        stderr = io.StringIO()
        stdout = io.StringIO()
        from contextlib import redirect_stderr, redirect_stdout

        with redirect_stderr(stderr), redirect_stdout(stdout):
            code = check_dist.main([str(self.dist), *args])
        return code, stderr.getvalue()

    def test_well_formed_distributions_pass(self) -> None:
        write_wheel(self.dist)
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual((code, errors), (0, ""))

    def test_missing_py_typed_and_template_file_fail(self) -> None:
        template_file = sorted(check_dist.template_files(ROOT))[0]
        write_wheel(self.dist, drop=("whykit/py.typed", template_file))
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("py.typed is missing", errors)
        self.assertIn(f"template file {template_file} is missing", errors)

    def test_tests_caches_and_bytecode_in_the_wheel_fail(self) -> None:
        write_wheel(self.dist, extra={
            "tests/test_x.py": "", "whykit/__pycache__/cli.cpython-311.pyc": "", "whykit/stray.pyc": "",
        })
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("unexpected top-level entry 'tests/test_x.py'", errors)
        self.assertIn("must not ship 'whykit/__pycache__/cli.cpython-311.pyc'", errors)
        self.assertIn("must not ship 'whykit/stray.pyc'", errors)

    def test_legacy_license_classifier_and_missing_expression_fail(self) -> None:
        bad = metadata(classifiers=("License :: OSI Approved :: Apache Software License",), license_expression=None)
        write_wheel(self.dist, meta=bad)
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("PEP 639 forbids", errors)
        self.assertIn("License-Expression is None", errors)

    def test_a_runtime_dependency_fails(self) -> None:
        write_wheel(self.dist, meta=metadata(requires=("requests>=2",)))
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("unexpected runtime dependency 'requests>=2'", errors)

    def test_relative_links_in_the_long_description_fail_but_code_fences_do_not(self) -> None:
        body = "See [rules](docs/rules.md) and ![x](docs/media/a.svg).\n\n```md\n[ok](inside/fence.md)\n```\n"
        write_wheel(self.dist, meta=metadata(body=body))
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("relative link 'docs/rules.md'", errors)
        self.assertIn("relative link 'docs/media/a.svg'", errors)
        self.assertNotIn("inside/fence.md", errors)

    def test_tests_stray_files_and_links_in_the_sdist_fail(self) -> None:
        write_wheel(self.dist)
        write_sdist(self.dist, extra={"tests/test_x.py": "", "scripts/README.md": ""}, symlink="src/whykit/link")
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("unexpected top-level entry 'tests/test_x.py'", errors)
        self.assertIn("unexpected top-level entry 'scripts/README.md'", errors)
        self.assertIn("is a link or special file", errors)

    def test_version_mismatch_and_extra_archives_fail(self) -> None:
        write_wheel(self.dist, version="9.9.9")
        write_wheel(self.dist)
        write_sdist(self.dist)
        code, errors = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("expected exactly one wheel", errors)
        self.assertIn("does not match version", errors)

    def test_reproducibility_compares_bytes(self) -> None:
        write_wheel(self.dist)
        write_sdist(self.dist)
        rebuild = self.tmp / "rebuild"
        rebuild.mkdir()
        for path in self.dist.iterdir():
            (rebuild / path.name).write_bytes(path.read_bytes())
        self.assertEqual(self.run_check("--reproducible-against", str(rebuild))[0], 0)
        wheel = next(rebuild.glob("*.whl"))
        wheel.write_bytes(wheel.read_bytes() + b"\0")
        code, errors = self.run_check("--reproducible-against", str(rebuild))
        self.assertEqual(code, 1)
        self.assertIn("is not reproducible", errors)

    def _sbom(self, components: list[dict[str, object]]) -> Path:
        path = self.tmp / "whykit.cdx.json"
        path.write_text(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": components}), encoding="utf-8")
        return path

    def test_sbom_must_list_whykit_at_the_built_version_and_nothing_else(self) -> None:
        write_wheel(self.dist)
        write_sdist(self.dist)
        ours = {"name": "whykit", "version": VERSION, "licenses": [{"license": {"id": "Apache-2.0"}}]}
        self.assertEqual(self.run_check("--sbom", str(self._sbom([ours])))[0], 0)
        code, errors = self.run_check("--sbom", str(self._sbom([{**ours, "version": "0.0.1"}, {"name": "requests"}])))
        self.assertEqual(code, 1)
        self.assertIn("component version is '0.0.1'", errors)
        self.assertIn("SBOM lists ['requests']", errors)
        code, errors = self.run_check("--sbom", str(self._sbom([])))
        self.assertIn("exactly one whykit component, found 0", errors)


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    def test_publish_job_only_downloads_what_the_build_job_checked(self) -> None:
        publish = self.release.split("\n  publish:\n", 1)[1]
        build = self.release.split("\n  build:\n", 1)[1].split("\n  publish:\n", 1)[0]
        self.assertNotIn("uv build", publish)
        self.assertIn("actions/download-artifact@", publish)
        self.assertIn("id-token: write", publish)
        self.assertNotIn("id-token: write", build)
        self.assertIn("environment: pypi", publish)
        self.assertIn("attestations: true", publish)
        self.assertIn("scripts/check_dist.py", build)
        self.assertIn("SOURCE_DATE_EPOCH", build)

    def test_ci_builds_twice_checks_distributions_and_keeps_an_sbom(self) -> None:
        lowest = PROJECT["requires-python"].removeprefix(">=")
        for needle in (
            "SOURCE_DATE_EPOCH",
            "scripts/check_dist.py dist --reproducible-against",
            "cyclonedx-py environment",
            "--sbom",
            "actions/upload-artifact@",
            "twine check --strict",
            f'--python "{lowest}"',
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.ci)
        self.assertIn(f'--python "{lowest}"', self.release, "the release SBOM must come from the lowest supported Python")


if __name__ == "__main__":
    unittest.main()
