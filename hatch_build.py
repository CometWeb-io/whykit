"""Build-time metadata hook: pin the PyPI README's GitHub links to the release.

``hatch-fancy-pypi-readme`` (configured in ``pyproject.toml``) rewrites the
README's relative links to absolute GitHub URLs on ``main``. That is right for
development builds, but a released version's PyPI page should keep showing the
documentation of that release, not whatever ``main`` says a year later.

This hook runs after it and repins those URLs to the release tag. The tag is
derived from the version, never from Git or the environment: the release
workflow refuses a tag that differs from ``__version__``, so a final or
pre-release version ``X.Y.Z`` is always built from tag ``vX.Y.Z``. Deriving it
from the version keeps the build reproducible and gives the sdist and the wheel
built from it the same README, with no Git checkout present. Development and
local versions (``.devN``, ``+local``) have no tag and keep ``main``.

Standard library only apart from hatchling itself, which is already the build
backend. Never installed with the package.
"""
from __future__ import annotations

import re
from typing import Any

REPOSITORY = "CometWeb-io/whykit"
DEFAULT_REF = "main"
_PREFIXES = (
    f"https://github.com/{REPOSITORY}/blob/",
    f"https://github.com/{REPOSITORY}/tree/",
    f"https://raw.githubusercontent.com/{REPOSITORY}/",
)
# A PEP 440 version without a dev or local segment, as hatchling normalizes it:
# 1.2.3, 1!2.0, 1.2.3rc1, 1.2.3.post1. Anything else stays on main.
_RELEASE_VERSION = re.compile(r"^(?:\d+!)?\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?$")


def readme_ref(version: str) -> str:
    """The Git ref the PyPI README of ``version`` should link to."""
    return f"v{version}" if _RELEASE_VERSION.match(version or "") else DEFAULT_REF


def pin_readme_links(text: str, version: str) -> str:
    """Repin repository URLs on ``main`` to the tag of a release ``version``."""
    ref = readme_ref(version)
    if ref == DEFAULT_REF:
        return text
    for prefix in _PREFIXES:
        text = text.replace(f"{prefix}{DEFAULT_REF}/", f"{prefix}{ref}/")
    return text


try:  # pragma: no cover - exercised by the real build, not the unit tests
    from hatchling.metadata.plugin.interface import MetadataHookInterface
except ImportError:  # the test suite imports the helpers without hatchling
    MetadataHookInterface = object  # type: ignore[assignment,misc]


class ReadmeReleaseRefHook(MetadataHookInterface):  # type: ignore[misc,valid-type]
    PLUGIN_NAME = "custom"

    def update(self, metadata: dict[str, Any]) -> None:
        readme = metadata.get("readme")
        if isinstance(readme, dict) and isinstance(readme.get("text"), str):
            readme["text"] = pin_readme_links(readme["text"], str(metadata.get("version", "")))
