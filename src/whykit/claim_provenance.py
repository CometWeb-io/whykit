"""Optional producer payload: claim links and exact-byte anchors, without vault I/O.

Byte matches never establish semantic support, reviewer identity or approval.
This module does not change Markdown, lint rules or the v1 machine reports.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

AnchorStatus = Literal["source_unavailable", "version_mismatch", "location_mismatch", "matched"]


def _object(value: object, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(keys))}")
    return value


def _text(value: object, name: str, pattern: str | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be UTF-8 encodable") from None
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise ValueError(f"{name} has an invalid format")


def _validate_link(value: object) -> dict[str, Any]:
    link = _object(value, {"evidence_id", "source_version", "location", "assessment"}, "link")
    _text(link["evidence_id"], "evidence_id", r"E-[0-9]{3,}")
    _text(link["source_version"], "source_version", r"sha256:[0-9a-f]{64}")
    location = _object(link["location"], {"start_byte", "end_byte", "quote"}, "location")
    start, end = location["start_byte"], location["end_byte"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError("location requires integer offsets 0 <= start_byte < end_byte")
    _text(location["quote"], "quote")
    if len(location["quote"].encode("utf-8")) != end - start:
        raise ValueError("quote UTF-8 byte length must equal end_byte - start_byte")
    assessment = _object(link["assessment"], {"kind", "by", "relation"}, "assessment")
    if assessment["kind"] not in ("model", "human"):
        raise ValueError("assessment kind must be model or human")
    _text(assessment["by"], "assessment by")
    if assessment["relation"] not in ("supports", "contradicts"):
        raise ValueError("assessment relation must be supports or contradicts")
    return link


def validate_claim(value: object) -> None:
    """Raise ValueError for malformed payloads; an explicit empty links list is valid.

    The format discriminator is independent of CLI contract_version. Unknown
    fields are rejected so misspelled anchors/assessments cannot be ignored.
    """
    claim = _object(value, {"format", "claim_id", "statement", "links"}, "claim")
    if claim["format"] != "whykit.claim-provenance/v1":
        raise ValueError("unsupported claim provenance format")
    _text(claim["claim_id"], "claim_id", r"C-[0-9]{3,}")
    _text(claim["statement"], "statement")
    if not isinstance(claim["links"], list):
        raise ValueError("links must be an array; use [] for no evidence links")
    for link in claim["links"]:
        _validate_link(link)


def verify_anchor(value: object, source: bytes | None) -> AnchorStatus:
    """Check one link against caller-supplied raw bytes; never resolve E-IDs or URLs.

    The caller owns evidence resolution and access control. A matched result only
    binds the quoted bytes to this digest; it does not authenticate the E-ID.
    """
    link = _validate_link(value)
    if source is None:
        return "source_unavailable"
    if not isinstance(source, bytes):
        raise ValueError("source must be raw bytes or None")
    if "sha256:" + hashlib.sha256(source).hexdigest() != link["source_version"]:
        return "version_mismatch"
    location = link["location"]
    if location["end_byte"] > len(source) or source[location["start_byte"]:location["end_byte"]] != location["quote"].encode("utf-8"):
        return "location_mismatch"
    return "matched"
