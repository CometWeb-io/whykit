"""Repository-local WhyKit policy configuration.

WhyKit intentionally has no user-global configuration.  A vault may optionally
carry ``whykit.toml`` so the same checkout has the same defaults and CI gates on
every machine and for every agent.
"""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILE = "whykit.toml"
FORMAT_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "format_version": FORMAT_VERSION,
    "evidence_access_age_days": {},
    "defaults": {
        "owner": "TODO",
        "sensitivity": "internal",
        "decision_review_days": 90,
        "status_due_days": 30,
        "require_hub_links": False,
    },
    "profiles": {
        "local": {
            "strict": False,
            "orphans": True,
            "secrets": True,
            "require_git": False,
            "require_clean_tree": False,
            "require_configured": False,
            "require_hub_links": False,
            "history": "optional",
        },
        "ci": {
            "strict": True,
            "orphans": True,
            "secrets": True,
            "require_git": False,
            "require_clean_tree": False,
            "require_configured": False,
            "require_hub_links": False,
            "history": "optional",
        },
        "release": {
            "strict": True,
            "orphans": True,
            "secrets": True,
            "require_git": True,
            "require_clean_tree": True,
            "require_configured": True,
            "require_hub_links": False,
            "history": "required",
        },
    },
}

_ALLOWED_SENSITIVITY = {"public", "internal", "confidential", "restricted"}
_ALLOWED_HISTORY = {"off", "optional", "required"}
_TOP_LEVEL_KEYS = {
    "format_version",
    "evidence_access_age_days",
    "defaults",
    "profiles",
}
_DEFAULT_KEYS = {
    "owner",
    "sensitivity",
    "decision_review_days",
    "status_due_days",
    "require_hub_links",
}
_PROFILE_KEYS = {
    "strict", "orphans", "secrets", "require_git", "require_clean_tree",
    "require_configured", "require_hub_links", "history",
}


class ConfigError(ValueError):
    pass


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate(config: dict[str, Any]) -> None:
    unknown_top = set(config) - _TOP_LEVEL_KEYS
    if unknown_top:
        raise ConfigError("unknown top-level keys: " + ", ".join(sorted(unknown_top)))

    version = config.get("format_version")
    if version != FORMAT_VERSION:
        raise ConfigError(f"unsupported whykit.toml format_version: {version!r}; expected {FORMAT_VERSION}")

    access_age = config.get("evidence_access_age_days")
    if not isinstance(access_age, dict):
        raise ConfigError("[evidence_access_age_days] must be a table")
    for source_type, days in access_age.items():
        if not isinstance(source_type, str) or not source_type.strip():
            raise ConfigError("evidence_access_age_days source type must be non-empty")
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise ConfigError(f"evidence_access_age_days.{source_type} must be an integer >= 0")

    defaults = config.get("defaults")
    if not isinstance(defaults, dict):
        raise ConfigError("[defaults] must be a table")
    unknown_defaults = set(defaults) - _DEFAULT_KEYS
    if unknown_defaults:
        raise ConfigError("unknown [defaults] keys: " + ", ".join(sorted(unknown_defaults)))
    owner = defaults.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        raise ConfigError("defaults.owner must be a non-empty string")
    sensitivity = defaults.get("sensitivity")
    if sensitivity not in _ALLOWED_SENSITIVITY:
        raise ConfigError("defaults.sensitivity must be public, internal, confidential or restricted")
    for key in ("decision_review_days", "status_due_days"):
        value = defaults.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"defaults.{key} must be an integer >= 0")
    if not isinstance(defaults.get("require_hub_links"), bool):
        raise ConfigError("defaults.require_hub_links must be true or false")

    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ConfigError("[profiles] must define at least one profile")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(profile, dict):
            raise ConfigError("each profile must be a named table")
        unknown = set(profile) - _PROFILE_KEYS
        if unknown:
            raise ConfigError(f"profile {name!r} has unknown keys: {', '.join(sorted(unknown))}")
        for key in (
            "strict", "orphans", "secrets", "require_git", "require_clean_tree",
            "require_configured", "require_hub_links",
        ):
            if not isinstance(profile.get(key), bool):
                raise ConfigError(f"profiles.{name}.{key} must be true or false")
        if profile.get("history") not in _ALLOWED_HISTORY:
            raise ConfigError(f"profiles.{name}.history must be off, optional or required")


def load_config(root: Path) -> tuple[dict[str, Any], Path | None]:
    """Load and validate repo-local config, falling back to deterministic defaults."""
    path = root / CONFIG_FILE
    if not path.exists():
        config = copy.deepcopy(DEFAULT_CONFIG)
        _validate(config)
        return config, None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {CONFIG_FILE}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_FILE} must contain a TOML table")
    unknown_raw_top = set(raw) - _TOP_LEVEL_KEYS
    if unknown_raw_top:
        raise ConfigError("unknown top-level keys: " + ", ".join(sorted(unknown_raw_top)))
    if "defaults" in raw:
        if not isinstance(raw["defaults"], dict):
            raise ConfigError("[defaults] must be a table")
        unknown_raw_defaults = set(raw["defaults"]) - _DEFAULT_KEYS
        if unknown_raw_defaults:
            raise ConfigError("unknown [defaults] keys: " + ", ".join(sorted(unknown_raw_defaults)))
    config = _merge(DEFAULT_CONFIG, raw)
    _validate(config)
    return config, path


def configuration_readiness(config: dict[str, Any], config_path: Path | None) -> tuple[bool, list[str]]:
    """Return whether repository policy is concretely configured for release use.

    This is intentionally stricter than syntactic validation: a generated policy
    containing starter placeholders is valid TOML but is not an operational
    release policy.
    """
    reasons: list[str] = []
    if config_path is None:
        reasons.append(f"{CONFIG_FILE} is missing; built-in defaults are not release configuration")
    owner = str(config.get("defaults", {}).get("owner", "")).strip()
    if owner.casefold() in {"todo", "tbd", "unknown", "unset", "n/a", "none"}:
        reasons.append("defaults.owner is still a placeholder")
    return not reasons, reasons


def get_profile(config: dict[str, Any], name: str) -> dict[str, Any]:
    profiles = config.get("profiles", {})
    if name not in profiles:
        raise ConfigError(f"unknown policy profile {name!r}; available: {', '.join(sorted(profiles))}")
    return copy.deepcopy(profiles[name])


def config_summary(root: Path) -> dict[str, Any]:
    config, path = load_config(root)
    source = "built-in defaults"
    if path is not None:
        source = path.resolve().relative_to(root.resolve()).as_posix()
    return {
        "contract_version": 1,
        "source": source,
        "configured": path is not None,
        "config": config,
    }
