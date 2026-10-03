#!/usr/bin/env python3
"""Check the built WhyKit distributions before anyone uploads them.

    python3 scripts/check_dist.py dist/
    python3 scripts/check_dist.py dist/ --reproducible-against dist-rebuild/
    python3 scripts/check_dist.py dist/ --sbom dist/whykit.cdx.json

`twine check` validates that metadata renders; it does not know what this
project promises. This script checks those promises on the real archives:

* exactly one wheel and one sdist, both for the version in ``__init__.py``;
* the wheel carries ``py.typed`` and every file of the vault template, and no
  tests, caches or compiled bytecode;
* PEP 639 metadata: ``License-Expression``, both license files, and no
  superseded ``License ::`` classifier;
* zero runtime dependencies (only the optional ``mcp`` extra);
* the PyPI long description has no relative links, which would 404 there,
  and its links into the repository use the release tag ``v<version>`` for a
  release and ``main`` for a development build;
* the sdist holds the documented allowlist and nothing from ``tests/``;
* optionally, byte-identical rebuilds and a CycloneDX SBOM that lists WhyKit
  at the built version and no other runtime component.

Standard library only, so it runs before any environment is synced. Exit 0 when
every check passes, 1 with one line per problem otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from email.parser import HeaderParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
NAME = "whykit"
LICENSE_EXPRESSION = "Apache-2.0"
LICENSE_FILES = ("LICENSE", "NOTICE")
ALLOWED_EXTRAS = {"mcp"}
# Top-level entries the sdist may contain, besides PKG-INFO and the
# .gitignore hatchling always adds. Mirrors [tool.hatch.build.targets.sdist].
SDIST_TOP_LEVEL = {
    "src", "schemas", "examples", "README.md", "CHANGELOG.md", "LICENSE", "NOTICE",
    "hatch_build.py", "pyproject.toml", "PKG-INFO", ".gitignore",
}
FORBIDDEN_PARTS = {"__pycache__", "tests", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}
# A Markdown link or image whose target has no URL scheme and is not an anchor.
RELATIVE_LINK = re.compile(r"\]\((?![a-zA-Z][a-zA-Z0-9+.-]*:|#)([^)\s]+)\)")
VERSION = re.compile(r'(?m)^__version__ = "([^"]+)"$')
# Links from the long description into this repository, and the ref they use.
REPO_LINK_REF = re.compile(
    r"https://(?:github\.com/CometWeb-io/whykit/(?:blob|tree)|raw\.githubusercontent\.com/CometWeb-io/whykit)/([^/\s)]+)/"
)
# A version without a dev or local segment is released from tag v<version>;
# mirrors readme_ref() in hatch_build.py.
RELEASE_VERSION = re.compile(r"^(?:\d+!)?\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?$")


def expected_readme_ref(version: str) -> str:
    return f"v{version}" if RELEASE_VERSION.match(version) else "main"


def package_version(root: Path = ROOT) -> str:
    text = (root / "src" / NAME / "__init__.py").read_text(encoding="utf-8")
    match = VERSION.search(text)
    if match is None:
        raise SystemExit("cannot find __version__ in src/whykit/__init__.py")
    return match.group(1)


def template_files(root: Path = ROOT) -> set[str]:
    """Template paths relative to the package, as tracked by Git when possible.

    Git's view excludes ignored local clutter (an editor's swap file, a
    ``.DS_Store``) that hatchling would also exclude, so the comparison stays
    exact. Outside a Git checkout, fall back to the file system.
    """
    template = root / "src" / NAME / "template"
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", str(template)],
            cwd=root, capture_output=True, check=True, timeout=30,
        ).stdout.decode("utf-8").split("\0")
        files = {
            (root / entry).relative_to(root / "src").as_posix()
            for entry in listed if entry
        }
        if files:
            return files
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        path.relative_to(root / "src").as_posix()
        for path in template.rglob("*")
        if path.is_file() and not FORBIDDEN_PARTS.intersection(path.parts)
    }


def _forbidden(names: list[str], *, strip_top: bool) -> list[str]:
    bad = []
    for name in names:
        parts = PurePosixPath(name).parts[1:] if strip_top else PurePosixPath(name).parts
        if FORBIDDEN_PARTS.intersection(parts) or name.endswith((".pyc", ".pyo")):
            bad.append(name)
    return bad


def check_metadata(text: str, version: str, where: str) -> list[str]:
    problems: list[str] = []
    message = HeaderParser().parsestr(text)
    if message.get("Name") != NAME:
        problems.append(f"{where}: Name is {message.get('Name')!r}, expected {NAME!r}")
    if message.get("Version") != version:
        problems.append(f"{where}: Version is {message.get('Version')!r}, expected {version!r}")
    if message.get("License-Expression") != LICENSE_EXPRESSION:
        problems.append(f"{where}: License-Expression is {message.get('License-Expression')!r}, expected {LICENSE_EXPRESSION!r}")
    license_files = set(message.get_all("License-File") or [])
    for expected in LICENSE_FILES:
        if expected not in license_files:
            problems.append(f"{where}: License-File {expected} is not declared")
    for classifier in message.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            problems.append(f"{where}: PEP 639 forbids pairing License-Expression with classifier {classifier!r}")
    if not message.get("Requires-Python"):
        problems.append(f"{where}: Requires-Python is missing")
    for requirement in message.get_all("Requires-Dist") or []:
        marker = re.search(r"extra\s*==\s*['\"]([^'\"]+)['\"]", requirement)
        if marker is None or marker.group(1) not in ALLOWED_EXTRAS:
            problems.append(f"{where}: unexpected runtime dependency {requirement!r}; the core package has none")
    if message.get("Description-Content-Type") != "text/markdown":
        problems.append(f"{where}: Description-Content-Type is {message.get('Description-Content-Type')!r}, expected text/markdown")
    body = message.get_payload()
    if not isinstance(body, str) or not body.strip():
        problems.append(f"{where}: long description is empty")
    else:
        body = re.sub(r"(?ms)^```.*?^```\s*", "", body)
        for target in RELATIVE_LINK.findall(body):
            problems.append(f"{where}: long description has relative link {target!r}, which 404s on PyPI")
        expected_ref = expected_readme_ref(version)
        for ref in sorted(set(REPO_LINK_REF.findall(body)) - {expected_ref}):
            problems.append(
                f"{where}: long description links to repository ref {ref!r}; version {version} must link to {expected_ref!r}"
            )
    return problems


def check_wheel(path: Path, version: str, root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        dist_info = f"{NAME}-{version}.dist-info"
        try:
            metadata = archive.read(f"{dist_info}/METADATA").decode("utf-8")
        except KeyError:
            return [f"{path.name}: {dist_info}/METADATA is missing"]
        problems += check_metadata(metadata, version, path.name)
        entry_points = archive.read(f"{dist_info}/entry_points.txt").decode("utf-8") if f"{dist_info}/entry_points.txt" in names else ""
    present = set(names)
    if f"{NAME}/py.typed" not in present:
        problems.append(f"{path.name}: {NAME}/py.typed is missing; type checkers would ignore the inline annotations")
    for license_file in LICENSE_FILES:
        if f"{dist_info}/licenses/{license_file}" not in present:
            problems.append(f"{path.name}: {dist_info}/licenses/{license_file} is missing")
    missing_template = sorted(template_files(root) - present)
    for name in missing_template:
        problems.append(f"{path.name}: template file {name} is missing; `whykit init` would write an incomplete vault")
    for name in names:
        top = name.split("/", 1)[0]
        if top not in {NAME, dist_info}:
            problems.append(f"{path.name}: unexpected top-level entry {name!r}")
    for name in _forbidden(names, strip_top=False):
        problems.append(f"{path.name}: must not ship {name!r}")
    for script in ("whykit = whykit.cli:main", "whykit-mcp = whykit.mcp_server:main"):
        if script not in entry_points:
            problems.append(f"{path.name}: console script {script!r} is not declared")
    return problems


def check_sdist(path: Path, version: str) -> list[str]:
    problems: list[str] = []
    prefix = f"{NAME}-{version}"
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        try:
            pkg_info = archive.extractfile(f"{prefix}/PKG-INFO")
        except KeyError:
            pkg_info = None
        if pkg_info is None:
            return [f"{path.name}: {prefix}/PKG-INFO is missing"]
        problems += check_metadata(pkg_info.read().decode("utf-8"), version, path.name)
    for member in members:
        if not (member.isfile() or member.isdir()):
            problems.append(f"{path.name}: {member.name!r} is a link or special file")
    for name in names:
        parts = PurePosixPath(name).parts
        if parts[0] != prefix:
            problems.append(f"{path.name}: {name!r} is outside {prefix}/")
            continue
        if len(parts) > 1 and parts[1] not in SDIST_TOP_LEVEL:
            problems.append(f"{path.name}: unexpected top-level entry {'/'.join(parts[1:])!r}")
    for name in _forbidden(names, strip_top=True):
        problems.append(f"{path.name}: must not ship {name!r}")
    for required in ("pyproject.toml", "hatch_build.py", "README.md", "LICENSE", "NOTICE", f"src/{NAME}/__init__.py", f"src/{NAME}/py.typed"):
        if f"{prefix}/{required}" not in names:
            problems.append(f"{path.name}: {required} is missing; the sdist cannot rebuild the wheel")
    return problems


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_reproducible(first: dict[str, Path], second_dir: Path) -> list[str]:
    problems: list[str] = []
    for name, path in sorted(first.items()):
        other = second_dir / name
        if not other.is_file():
            problems.append(f"rebuild has no {name}")
        elif sha256(path) != sha256(other):
            problems.append(f"{name} is not reproducible: {sha256(path)} != {sha256(other)} (is SOURCE_DATE_EPOCH set for both builds?)")
    return problems


def check_sbom(path: Path, version: str) -> list[str]:
    try:
        bom = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path.name}: cannot read SBOM ({exc})"]
    problems: list[str] = []
    if bom.get("bomFormat") != "CycloneDX":
        problems.append(f"{path.name}: bomFormat is {bom.get('bomFormat')!r}, expected 'CycloneDX'")
    components = bom.get("components") or []
    ours = [c for c in components if c.get("name") == NAME]
    if len(ours) != 1:
        problems.append(f"{path.name}: expected exactly one {NAME} component, found {len(ours)}")
    elif ours[0].get("version") != version:
        problems.append(f"{path.name}: {NAME} component version is {ours[0].get('version')!r}, expected {version!r}")
    else:
        licenses = {
            (entry.get("license") or {}).get("id") or entry.get("expression")
            for entry in ours[0].get("licenses") or []
        }
        if LICENSE_EXPRESSION not in licenses:
            problems.append(f"{path.name}: {NAME} component does not declare {LICENSE_EXPRESSION}")
    others = sorted(c.get("name", "?") for c in components if c.get("name") != NAME)
    if others:
        problems.append(f"{path.name}: core install should have no runtime dependencies, SBOM lists {others}")
    return problems


def find_distributions(dist: Path, version: str) -> tuple[dict[str, Path], list[str]]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    problems: list[str] = []
    if len(wheels) != 1:
        problems.append(f"expected exactly one wheel in {dist}, found {len(wheels)}")
    if len(sdists) != 1:
        problems.append(f"expected exactly one sdist in {dist}, found {len(sdists)}")
    found: dict[str, Path] = {}
    expected = {f"{NAME}-{version}-py3-none-any.whl", f"{NAME}-{version}.tar.gz"}
    for path in [*wheels, *sdists]:
        if path.name not in expected:
            problems.append(f"{path.name} does not match version {version} (expected one of {sorted(expected)})")
        found[path.name] = path
    return found, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check built WhyKit wheel and sdist against the project's packaging promises.")
    parser.add_argument("dist", type=Path, help="directory holding the wheel and sdist from `uv build`")
    parser.add_argument("--reproducible-against", type=Path, metavar="DIR", help="a second build of the same commit; every archive must be byte-identical")
    parser.add_argument("--sbom", type=Path, metavar="FILE", help="CycloneDX JSON SBOM of a clean core install to validate")
    parser.add_argument("--version", dest="expected_version", help="expected version (default: __version__ from the checkout)")
    args = parser.parse_args(argv)

    version = args.expected_version or package_version()
    found, problems = find_distributions(args.dist, version)
    for path in found.values():
        if path.name.endswith(".whl"):
            problems += check_wheel(path, version)
        else:
            problems += check_sdist(path, version)
    if args.reproducible_against is not None:
        problems += check_reproducible(found, args.reproducible_against)
    if args.sbom is not None:
        problems += check_sbom(args.sbom, version)

    for problem in problems:
        print(f"check_dist: {problem}", file=sys.stderr)
    if problems:
        return 1
    for name, path in sorted(found.items()):
        print(f"{sha256(path)}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
