"""Optional read-only MCP server for WhyKit vaults.

Install with ``pip install 'whykit[mcp]'`` (or ``uv sync --extra mcp``). The core
WhyKit package stays dependency-free; this module imports ``mcp`` only when the
server is started.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SENSITIVITY_LEVEL = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}
MAX_MCP_CONTEXT = 50_000
MAX_MCP_RESULTS = 100
DEFAULT_MAX_SENSITIVITY = "internal"


def _require_mcp():
    try:
        from mcp.server import MCPServer
    except ImportError as exc:  # pragma: no cover - exercised when extra missing
        raise SystemExit(
            "MCP support requires the optional extra: pip install 'whykit[mcp]'"
        ) from exc
    return MCPServer


def _validate_context_limit(value: int) -> int:
    if value < 0:
        raise ValueError("max_chars must be >= 0")
    return min(value, MAX_MCP_CONTEXT)


def _validate_result_limit(value: int) -> int:
    if value < 0:
        raise ValueError("limit must be >= 0")
    return min(value, MAX_MCP_RESULTS)


def _sensitivity_level(value: object) -> int:
    key = str(value or "internal").lower() or "internal"
    return SENSITIVITY_LEVEL.get(key, 99)


def _visible(item: dict, policy: str) -> bool:
    return _sensitivity_level(item.get("sensitivity")) <= SENSITIVITY_LEVEL[policy]


def _allowed_sensitivities(policy: str) -> set[str]:
    ceiling = SENSITIVITY_LEVEL[policy]
    return {name for name, level in SENSITIVITY_LEVEL.items() if level <= ceiling}


def _filter_nested(value: object, policy: str) -> object:
    """Recursively drop dict nodes whose sensitivity exceeds *policy*."""
    if isinstance(value, dict):
        # Evidence-register rows do not yet have a per-entry sensitivity field;
        # they inherit the register's `internal` classification. Fail closed
        # under a public-only ceiling rather than returning source/claim details.
        if (
            policy == "public"
            and isinstance(value.get("id"), str)
            and value["id"].startswith("E-")
            and {"state", "record"}.issubset(value)
        ):
            return None
        # Evidence detail wrappers nest the register row under "record".
        if "sensitivity" in value and not _visible(value, policy):
            return None
        nested_record = value.get("record")
        if isinstance(nested_record, dict) and "sensitivity" in nested_record and not _visible(nested_record, policy):
            return None
        out: dict = {}
        for key, child in value.items():
            filtered = _filter_nested(child, policy)
            if filtered is None and isinstance(child, dict):
                continue
            out[key] = filtered
        return out
    if isinstance(value, list):
        filtered_items = []
        for item in value:
            filtered = _filter_nested(item, policy)
            if filtered is None and isinstance(item, dict):
                continue
            filtered_items.append(filtered)
        return filtered_items
    return value


def filter_report(report: dict, policy: str) -> dict:
    """Enforce sensitivity on the root record and every nested graph expansion."""
    record = report.get("record")
    if isinstance(record, dict) and (
        "sensitivity" in record or report.get("kind") in {"decision", "document"}
    ):
        if not _visible(record, policy):
            raise PermissionError(
                f"document sensitivity {str(record.get('sensitivity') or 'internal')!r} "
                f"exceeds MCP policy ({policy})"
            )

    # Evidence targets have no document sensitivity today; default them to internal
    # so a restricted-only reference graph cannot leak through an E-NNN lookup.
    if report.get("kind") == "evidence" and _sensitivity_level("internal") > SENSITIVITY_LEVEL[policy]:
        raise PermissionError(f"evidence targets exceed MCP policy ({policy})")

    filtered = _filter_nested(dict(report), policy)
    assert isinstance(filtered, dict)
    return filtered


def build_server(vault: Path, *, max_sensitivity: str = DEFAULT_MAX_SENSITIVITY):
    MCPServer = _require_mcp()
    from whykit.context import build_context
    from whykit.impact import analyze_impact
    from whykit.lint import is_vault_root
    from whykit.query import query_vault

    if not is_vault_root(vault):
        raise SystemExit(f"not a WhyKit vault: {vault}")
    policy = max_sensitivity.lower()
    if policy not in SENSITIVITY_LEVEL:
        raise SystemExit(
            f"unsupported --max-sensitivity {max_sensitivity!r}; "
            f"choose one of: {', '.join(SENSITIVITY_LEVEL)}"
        )
    allowed = _allowed_sensitivities(policy)

    mcp = MCPServer("whykit")

    @mcp.tool()
    def query(text: str = "", limit: int = 20) -> str:
        """Ranked search across the vault (read-only)."""
        limit = _validate_result_limit(limit)
        report = query_vault(
            vault,
            text=text or None,
            limit=limit,
            allowed_sensitivities=allowed,
        )
        return json.dumps({**report, "max_sensitivity": policy}, ensure_ascii=False, indent=2)

    @mcp.tool()
    def context(target: str, max_chars: int = 4000) -> str:
        """Bounded context pack for one evidence ID, decision or document."""
        max_chars = _validate_context_limit(max_chars)
        report = build_context(vault, target, max_chars=max_chars, include_body=True)
        try:
            filtered = filter_report(report, policy)
        except PermissionError:
            # Match the ordinary missing-target contract so a caller cannot
            # confirm the existence or sensitivity of a filtered document.
            filtered = {
                "contract_version": 1,
                "target": target,
                "exists": False,
                "ambiguous": False,
                "kind": report.get("kind", "document"),
            }
        return json.dumps(filtered, ensure_ascii=False, indent=2)

    @mcp.tool()
    def impact(target: str) -> str:
        """Reverse dependency / blast radius for E-NNN, D-NNN or a document."""
        report = analyze_impact(vault, target)
        try:
            filtered = filter_report(report, policy)
        except PermissionError:
            # Return the same shape as an absent target of this kind. In
            # particular, do not reveal a hidden record's sensitivity label.
            kind = report.get("kind", "document")
            if kind == "evidence":
                filtered = {
                    "contract_version": 1,
                    "target": target,
                    "kind": kind,
                    "exists": False,
                    "state": "missing",
                    "record": None,
                    "replacement": None,
                    "references": [],
                    "reference_count": 0,
                }
            elif kind == "decision":
                filtered = {
                    "contract_version": 1,
                    "target": target,
                    "kind": kind,
                    "exists": False,
                    "ambiguous": False,
                    "references": [],
                    "reference_count": 0,
                }
            else:
                filtered = {
                    "contract_version": 1,
                    "target": target,
                    "kind": "document",
                    "exists": False,
                    "ambiguous": False,
                    "incoming": [],
                    "outgoing": [],
                    "reference_count": 0,
                }
        return json.dumps(filtered, ensure_ascii=False, indent=2)

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whykit-mcp", description="Read-only WhyKit MCP server")
    parser.add_argument("--root", required=True, help="path to a WhyKit vault")
    parser.add_argument(
        "--max-sensitivity",
        default=DEFAULT_MAX_SENSITIVITY,
        choices=tuple(SENSITIVITY_LEVEL),
        help="highest sensitivity documents MCP may return (default: internal)",
    )
    args = parser.parse_args(argv)
    vault = Path(args.root).expanduser().resolve()
    server = build_server(vault, max_sensitivity=args.max_sensitivity)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
