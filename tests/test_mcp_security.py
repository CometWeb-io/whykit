"""MCP sensitivity contract — Broken Access Control regressions."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "whykit.py"
sys.path.insert(0, str(ROOT / "src"))

from whykit.mcp_server import filter_report  # noqa: E402
from whykit.query import query_vault  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], text=True, capture_output=True)


def _write_doc(vault: Path, relative: str, *, title: str, sensitivity: str, body: str = "") -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
title: {title}
aliases: []
type: guide
status: approved
owner: Tester
created: 2026-09-23
last_updated: 2026-09-23
source_of_truth: false
sensitivity: {sensitivity}
source_ids: []
tags: []
---

# {title}

{body}
""",
        encoding="utf-8",
    )


class McpSensitivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.assertEqual(run("init", "--minimal", str(self.vault)).returncode, 0)
        _write_doc(
            self.vault,
            "notes/public-note.md",
            title="Public Alpha",
            sensitivity="public",
            body="needle-alpha public",
        )
        _write_doc(
            self.vault,
            "notes/internal-note.md",
            title="Internal Beta",
            sensitivity="internal",
            body="needle-alpha internal",
        )
        _write_doc(
            self.vault,
            "notes/restricted-secret.md",
            title="Restricted Gamma",
            sensitivity="restricted",
            body="needle-alpha restricted classified",
        )
        # Internal hub that links to the restricted document.
        _write_doc(
            self.vault,
            "notes/hub.md",
            title="Hub",
            sensitivity="internal",
            body="See [[notes/restricted-secret]] for the classified details.",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mcp_query_hides_restricted_results(self) -> None:
        report = query_vault(
            self.vault,
            text="needle-alpha",
            limit=50,
            allowed_sensitivities={"public", "internal"},
        )
        paths = {item["path"] for item in report["results"]}
        self.assertIn("notes/public-note.md", paths)
        self.assertIn("notes/internal-note.md", paths)
        self.assertNotIn("notes/restricted-secret.md", paths)
        # Exact title match must still not surface restricted docs.
        exact = query_vault(
            self.vault,
            text="Restricted Gamma",
            limit=50,
            allowed_sensitivities={"public", "internal"},
        )
        self.assertEqual(exact["results"], [])

    def test_mcp_context_hides_restricted_backlinks(self) -> None:
        from whykit.impact import analyze_impact

        raw = analyze_impact(self.vault, "notes/hub")
        self.assertTrue(any(
            item.get("id") == "notes/restricted-secret" or item.get("path") == "notes/restricted-secret.md"
            for item in (raw.get("outgoing") or [])
        ))
        filtered = filter_report(raw, "internal")
        outgoing_ids = {
            item.get("id") or item.get("path")
            for item in (filtered.get("outgoing") or [])
        }
        self.assertNotIn("notes/restricted-secret", outgoing_ids)
        self.assertNotIn("notes/restricted-secret.md", outgoing_ids)

    def test_mcp_impact_does_not_leak_restricted_titles(self) -> None:
        from whykit.impact import analyze_impact

        raw = analyze_impact(self.vault, "notes/restricted-secret")
        with self.assertRaises(PermissionError):
            filter_report(raw, "internal")

    def test_mcp_evidence_respects_public_ceiling(self) -> None:
        report = {
            "contract_version": 1,
            "kind": "evidence",
            "exists": True,
            "record": {"id": "E-001", "source": "x"},
            "references": [
                {"path": "notes/restricted-secret.md", "title": "Restricted Gamma", "sensitivity": "restricted"},
                {"path": "notes/hub.md", "title": "Hub", "sensitivity": "internal"},
            ],
        }
        with self.assertRaises(PermissionError):
            filter_report(report, "public")
        filtered = filter_report(report, "internal")
        paths = {item["path"] for item in filtered["references"]}
        self.assertEqual(paths, {"notes/hub.md"})

    def test_public_context_does_not_expose_unclassified_evidence_register_details(self) -> None:
        report = {
            "contract_version": 1,
            "target": "notes/public-note",
            "exists": True,
            "kind": "document",
            "record": {"path": "notes/public-note.md", "sensitivity": "public"},
            "evidence": [
                {
                    "id": "E-001",
                    "state": "active",
                    "record": {
                        "source": "Private interview",
                        "claims": "unclassified-evidence-sentinel",
                    },
                }
            ],
        }
        filtered = filter_report(report, "public")
        self.assertEqual(filtered["evidence"], [])
        self.assertNotIn("unclassified-evidence-sentinel", json.dumps(filtered))

    def test_server_build_uses_the_current_mcp_sdk_server_api(self) -> None:
        registered_tools: dict[str, object] = {}

        class SDKServer:
            def __init__(self, name: str) -> None:
                self.name = name

            def tool(self):
                def register(function):
                    registered_tools[function.__name__] = function
                    return function

                return register

        package = types.ModuleType("mcp")
        package.__path__ = []  # type: ignore[attr-defined]
        server_package = types.ModuleType("mcp.server")
        server_package.__path__ = []  # type: ignore[attr-defined]
        server_package.MCPServer = SDKServer  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {
            "mcp": package,
            "mcp.server": server_package,
        }):
            from whykit.mcp_server import build_server

            server = build_server(self.vault)

        self.assertEqual(server.name, "whykit")
        self.assertEqual(set(registered_tools), {"query", "context", "impact"})


if __name__ == "__main__":
    unittest.main()
