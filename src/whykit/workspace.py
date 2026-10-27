"""Local-only, read-only status across explicitly listed vaults."""
from __future__ import annotations

import datetime as dt
import os
import re
import stat
import sys
import tomllib
from pathlib import Path

from .config import ConfigError, load_config
from .io import vault_read_lock
from .lint import is_vault_root
from .status import build_status
from .vault_index import NoteCache, reuse_notes


class WorkspaceError(ValueError):
    """A fixed error code, never manifest values or underlying exception text."""


def _local_path(path: Path) -> Path:
    path = path.absolute()
    for current in reversed((path, *path.parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            # macOS exposes these OS directories through root-level aliases.
            if sys.platform == "darwin" and current != path and str(current) in {"/var", "/tmp", "/etc"}:
                continue
            raise WorkspaceError("unsafe_path")
    return path.resolve()


def load_workspace(manifest: Path) -> list[tuple[str, Path]]:
    """Read a local manifest; reject ambiguous aliases and overlapping roots."""
    try:
        manifest = _local_path(manifest)
        if not manifest.is_file():
            raise WorkspaceError("invalid_manifest")
        data = tomllib.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise WorkspaceError("invalid_manifest") from None
    if set(data) != {"workspace_version", "audience", "vaults"}:
        raise WorkspaceError("invalid_manifest")
    if type(data["workspace_version"]) is not int or data["workspace_version"] != 1:
        raise WorkspaceError("invalid_manifest")
    if data["audience"] != "local":
        raise WorkspaceError("local_only")
    entries = data["vaults"]
    if not isinstance(entries, list) or not entries:
        raise WorkspaceError("invalid_manifest")
    result: list[tuple[str, Path]] = []
    aliases: set[str] = set()
    identities: set[tuple[int, int]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"alias", "root"}:
            raise WorkspaceError("invalid_manifest")
        alias, raw = entry["alias"], entry["root"]
        if not isinstance(alias, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", alias):
            raise WorkspaceError("invalid_manifest")
        if alias.casefold() in aliases:
            raise WorkspaceError("duplicate_alias")
        aliases.add(alias.casefold())
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise WorkspaceError("invalid_manifest")
        try:
            root = _local_path(manifest.parent / raw)
            info = root.stat() if root.exists() else None
        except (OSError, RuntimeError):
            raise WorkspaceError("unsafe_path") from None
        if any(root.is_relative_to(other) or other.is_relative_to(root) for _, other in result):
            raise WorkspaceError("overlapping_roots")
        if info is not None:
            identity = (info.st_dev, info.st_ino)
            if identity in identities:
                raise WorkspaceError("overlapping_roots")
            identities.add(identity)
        result.append((alias, root))
    return sorted(result, key=lambda item: item[0].casefold())


def _regular_tree(root: Path) -> None:
    def fail(exc: OSError) -> None:
        raise exc

    # Refuse redirects and special files before the shared reader opens them.
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + files:
            info = (Path(directory) / name).lstat()
            if (not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise WorkspaceError("unsafe_path")
        dirs[:] = [name for name in dirs if name != ".git"]


def _vault_status(root: Path, today: dt.date) -> dict:
    try:
        _local_path(root)
        if not is_vault_root(root):
            return {"error": "vault_not_found"}
        _regular_tree(root)
        # An ambient CLI parse-cache scope must not turn this read into a write.
        with vault_read_lock(root), reuse_notes(NoteCache()):
            if not is_vault_root(root):
                return {"error": "vault_not_found"}
            config, _ = load_config(root)
            due_days = int(config["defaults"]["status_due_days"])
            report = build_status(root, today=today, due_days=due_days)
            return {"status": {
                "documents": report["documents"],
                "errors": report["errors"],
                "warnings": report["warnings"],
                "review_due": len(report["review_queue"]),
                "review_overdue": report["review_overdue"],
                "due_days": due_days,
            }}
    except WorkspaceError:
        return {"error": "unsafe_path"}
    except ConfigError:
        return {"error": "invalid_config"}
    except FileNotFoundError:
        return {"error": "vault_not_found"}
    except (OSError, UnicodeError, RuntimeError, ValueError, OverflowError):
        return {"error": "read_failed"}


def build_workspace_status(manifest: Path, *, today: dt.date | None = None) -> dict:
    """Report local counts only; labels are not authorization or public consent.

    Each vault has its own read lock. This is not an atomic cross-vault snapshot.
    External editors that ignore those locks remain outside the read guarantee.
    """
    entries = load_workspace(manifest)
    today = today or dt.date.today()
    return {
        "contract_version": 1,
        "audience": "local",
        "as_of": today.isoformat(),
        "vaults": [{"alias": alias, **_vault_status(root, today)} for alias, root in entries],
    }
