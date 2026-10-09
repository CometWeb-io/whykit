"""A small JSON Schema (draft 2020-12) validator for the schemas WhyKit ships.

WhyKit has no runtime dependencies and its test suite stays on the standard
library too. This validator implements only the keywords ``schemas/`` uses;
``SUPPORTED_KEYWORDS`` is checked by the tests, so a schema that starts using a
keyword this file does not understand fails loudly instead of validating
nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ANNOTATIONS = frozenset({"$schema", "$id", "title", "description", "$defs", "$comment", "examples", "default"})
ASSERTIONS = frozenset({
    "type", "const", "enum", "required", "properties", "additionalProperties",
    "items", "minItems", "pattern", "minLength", "minimum", "anyOf", "allOf", "$ref",
    "if", "then", "else", "uniqueItems",
})
SUPPORTED_KEYWORDS = ANNOTATIONS | ASSERTIONS

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def unsupported_keywords(schema: Any, path: str = "#") -> list[str]:
    """Every keyword in ``schema`` that this validator would silently ignore."""
    found: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key not in SUPPORTED_KEYWORDS:
                found.append(f"{path}/{key}")
            if key in ("properties", "$defs"):
                for name, sub in value.items():
                    found += unsupported_keywords(sub, f"{path}/{key}/{name}")
            elif key in ("items", "additionalProperties", "if", "then", "else") and isinstance(value, dict):
                found += unsupported_keywords(value, f"{path}/{key}")
            elif key in ("anyOf", "allOf"):
                for index, sub in enumerate(value):
                    found += unsupported_keywords(sub, f"{path}/{key}/{index}")
    return found


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Return human-readable violations; an empty list means valid."""
    errors: list[str] = []
    _check(instance, schema, schema, "$", errors)
    return errors


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        if not ref.startswith("https://cometweb.io/schemas/whykit/"):
            raise ValueError(f"only bundled $ref is supported: {ref}")
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / ref.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
        if schema.get("$id") != ref:
            raise ValueError(f"bundled schema ID does not match: {ref}")
        return schema
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value: Any, schema: Any, root: dict[str, Any], where: str, errors: list[str]) -> None:
    if schema is True or schema == {}:
        return
    if schema is False:
        errors.append(f"{where}: no value is allowed here")
        return
    if "$ref" in schema:
        resolved = _resolve(root, schema["$ref"])
        _check(value, resolved, root if schema["$ref"].startswith("#/") else resolved, where, errors)
    if "anyOf" in schema:
        if not any(not validate_with_root(value, sub, root, where) for sub in schema["anyOf"]):
            errors.append(f"{where}: matches none of anyOf")
    for sub in schema.get("allOf", ()):
        _check(value, sub, root, where, errors)
    if "if" in schema:
        branch = "then" if not validate_with_root(value, schema["if"], root, where) else "else"
        if branch in schema:
            _check(value, schema[branch], root, where, errors)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[name](value) for name in types):
            errors.append(f"{where}: expected {'/'.join(types)}, got {type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if _TYPES["number"](value) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{where}: {value} is below {schema['minimum']}")
    if isinstance(value, list) and "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{where}: fewer than {schema['minItems']} items")
    if isinstance(value, list) and schema.get("uniqueItems"):
        seen = [repr(item) for item in value]
        if len(seen) != len(set(seen)):
            errors.append(f"{where}: items are not unique")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], root, f"{where}[{index}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{where}: missing required key {key!r}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], root, f"{where}.{key}", errors)
            else:
                _check(item, extra, root, f"{where}.{key}", errors)


def validate_with_root(value: Any, schema: Any, root: dict[str, Any], where: str) -> list[str]:
    errors: list[str] = []
    _check(value, schema, root, where, errors)
    return errors
