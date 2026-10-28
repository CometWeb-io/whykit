"""Additive knowledge v2 and exact-byte, copy-on-write vault migration.

Portable data excludes Git and transient machine state. Hashes bind content;
none authenticates a reviewer or grants permission to execute an action.
Build/export/restore require POSIX descriptor-relative no-follow primitives and
refuse before writes elsewhere; the typed projection uses existing portable readers.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from typing import Any
import unicodedata

from .io import open_regular_binary, vault_read_lock
from .lint import Note
from .vault_index import NoteCache, VaultIndex, reuse_notes

MAX_FILES = 25_000
MAX_OBJECTS = 100_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RAW_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 96 * 1024 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 2_000_000
_NATIVE_IO_AVAILABLE = (os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
                        and all(function in os.supports_dir_fd for function in (os.open, os.mkdir, os.stat, os.link, os.unlink, os.rmdir))
                        and os.scandir in os.supports_fd)
MANIFEST_PATH = ".whykit/knowledge-v2.json"
EXCLUSIONS = [".git", ".whykit/cache", ".whykit/mutation.lock", ".whykit/transactions", MANIFEST_PATH]


class UnsupportedMigrationPlatform(ValueError):
    """Native no-follow migration I/O is unavailable on this host."""


def _wire(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid knowledge JSON") from exc


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bounded(value: Any) -> None:
    remaining = MAX_JSON_NODES
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        remaining -= 1
        if remaining < 0 or depth > MAX_JSON_DEPTH:
            raise ValueError("knowledge structure limit exceeded")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("knowledge object keys must be text")
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > MAX_BUNDLE_BYTES:
                raise ValueError("knowledge string limit exceeded")
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("knowledge strings must be UTF-8") from exc
        elif item is not None and type(item) not in (bool, int, float):
            raise ValueError("invalid knowledge value")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("nonfinite knowledge value")
        elif type(item) is int and item.bit_length() > 256:
            raise ValueError("knowledge integer limit exceeded")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate knowledge JSON key")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("nonfinite knowledge value")


def _supplied(value: Any) -> dict:
    if isinstance(value, (str, bytes)):
        raw = value.encode("utf-8") if isinstance(value, str) else value
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("knowledge bundle byte limit exceeded")
        try:
            value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        except (UnicodeError, RecursionError, OverflowError) as exc:
            raise ValueError("invalid knowledge JSON") from exc
    _bounded(value)
    if type(value) is not dict or len(_wire(value)) > MAX_BUNDLE_BYTES:
        raise ValueError("invalid or oversized knowledge bundle")
    return value


def _path(value: Any) -> str:
    if (type(value) is not str or not value or len(value) > 1024 or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid portable source path")
    parts = value.split("/")
    if any(part in ("", ".", "..") or ":" in part or part.endswith((" ", "."))
           or len(part) > 255 or part.casefold() == ".git"
           or re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0]) is not None
           for part in parts):
        raise ValueError("unsafe portable source path")
    if parts[0].casefold() == ".whykit":
        raise ValueError("machine state cannot be a portable source path")
    value.encode("utf-8")
    return value


def _collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _check_paths(names: list[str]) -> None:
    files: set[str] = set()
    directories: set[str] = set()
    spellings: dict[str, str] = {}
    for name in names:
        _path(name)
        for at in range(1, len(name.split("/")) + 1):
            prefix = "/".join(name.split("/")[:at])
            folded = _collision_key(prefix)
            if folded in spellings and spellings[folded] != prefix:
                raise ValueError("ambiguous portable source paths")
            spellings[folded] = prefix
        key = _collision_key(name)
        parents = ["/".join(key.split("/")[:at]) for at in range(1, len(key.split("/")))]
        if key in files or key in directories or any(parent in files for parent in parents):
            raise ValueError("ambiguous portable source paths")
        files.add(key)
        directories.update(parents)


def _check_inventory_paths(files: list[str], directories: list[str]) -> None:
    _check_paths(files)
    seen: dict[str, str] = {}
    file_keys = {_collision_key(name) for name in files}
    directory_set = set(directories)
    if len(directory_set) != len(directories):
        raise ValueError("duplicate migration directory")
    for name in [*directories, *files]:
        _path(name)
        parts = name.split("/")
        for at in range(1, len(parts) + 1):
            prefix = "/".join(parts[:at])
            key = _collision_key(prefix)
            if key in seen and seen[key] != prefix:
                raise ValueError("ambiguous portable source paths")
            seen[key] = prefix
            if at < len(parts) and (prefix not in directory_set or key in file_keys):
                raise ValueError("missing or conflicting migration directory")
        if name in directory_set and _collision_key(name) in file_keys:
            raise ValueError("file and directory conflict")


def _read(path: Path, *, limit: int = MAX_FILE_BYTES) -> bytes:
    with open_regular_binary(path) as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("portable source file byte limit exceeded")
    return raw


def _inventory(root: Path, *, restored: bool = False) -> tuple[dict[str, bytes], bytes | None, list[str]]:
    files: dict[str, bytes] = {}
    directories = []
    derived = None
    total = 0
    pending = [""]
    visited = 0
    root_fd = _open_directory(root)
    try:
        while pending:
            directory = pending.pop()
            descriptor = os.dup(root_fd)
            try:
                for part in directory.split("/") if directory else []:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                with os.scandir(descriptor) as iterator:
                    entries: list[os.DirEntry[str]] = []
                    for entry in iterator:
                        if visited + len(entries) >= MAX_FILES * 4:
                            raise ValueError("migration directory entry limit exceeded")
                        entries.append(entry)
                    for entry in sorted(entries, key=lambda item: item.name):
                        visited += 1
                        if visited > MAX_FILES * 4:
                            raise ValueError("migration directory entry limit exceeded")
                        name = directory + "/" + entry.name if directory else entry.name
                        if name == ".git":
                            if restored:
                                raise ValueError("unexpected restored machine state")
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            raise ValueError("symlinked migration input refused")
                        if name == ".whykit":
                            if not stat.S_ISDIR(info.st_mode):
                                raise ValueError("invalid migration machine state")
                            pending.append(name)
                            continue
                        if name.startswith(".whykit/"):
                            if name in (".whykit/cache", ".whykit/transactions") and stat.S_ISDIR(info.st_mode):
                                if restored:
                                    raise ValueError("unexpected restored machine state")
                                continue
                            if name == ".whykit/mutation.lock" and stat.S_ISREG(info.st_mode):
                                if restored:
                                    raise ValueError("unexpected restored machine state")
                                continue
                            if name == MANIFEST_PATH and stat.S_ISREG(info.st_mode):
                                derived = _read_at(descriptor, entry.name, limit=MAX_BUNDLE_BYTES)
                                continue
                            raise ValueError("unknown migration machine state")
                        _path(name)
                        if stat.S_ISDIR(info.st_mode):
                            directories.append(name)
                            pending.append(name)
                        elif stat.S_ISREG(info.st_mode):
                            raw = _read_at(descriptor, entry.name, limit=MAX_FILE_BYTES)
                            total += len(raw)
                            if len(files) >= MAX_FILES or total > MAX_RAW_BYTES:
                                raise ValueError("migration source inventory limit exceeded")
                            files[name] = raw
                        else:
                            raise ValueError("nonregular migration input refused")
            finally:
                os.close(descriptor)
    finally:
        os.close(root_fd)
    _check_inventory_paths(sorted(files), sorted(directories))
    return dict(sorted(files.items())), derived, sorted(directories)


def _read_at(parent_fd: int, name: str, *, limit: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("nonregular migration input refused")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("portable source file byte limit exceeded")
    return raw


def _write_raw(root: Path, files: dict[str, bytes], directories: list[str]) -> None:
    # Only called in a fresh private temporary directory after all paths validate.
    for name in directories:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())


def _manifest(files: dict[str, bytes], directories: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="whykit-knowledge-validate-") as directory:
        staged = Path(directory).resolve()
        _write_raw(staged, files, directories)
        return _project(staged, set(_LABELS), None, full=True)


def _native_coverage(manifest: dict, files: dict[str, bytes]) -> None:
    mapped = {item["source"]["path"] for item in manifest["objects"]}
    for name in files:
        native = name.startswith(("00-context/claims/", "00-context/outcomes/", "00-context/artifacts/"))
        native = native or re.fullmatch(r"06-decisions/d-[0-9]{3,}-.+\.[mM][dD]", name) is not None
        if native and name not in mapped:
            raise ValueError("unconverted native knowledge source")


def build_migration_bundle(root: Path, *, full_local_scope: bool = False,
                           allowed_sensitivities: set[str] | None = None,
                           vault: VaultIndex | None = None) -> dict:
    """Capture raw local source bytes; explicit full scope is mandatory, never implicit."""
    if full_local_scope is not True or allowed_sensitivities is not None or vault is not None:
        raise ValueError("lossless migration requires explicit full local scope without a restricted grant")
    _require_native_io()
    root = Path(root).resolve(strict=True)
    with vault_read_lock(root), reuse_notes(NoteCache()):
        files, derived, directories = _inventory(root)
        manifest = _manifest(files, directories)
        _native_coverage(manifest, files)
        if derived is not None and derived != _wire(manifest):
            raise ValueError("stale or invalid derived knowledge manifest")
    result = {"contract_version": 1, "format": "whykit.knowledge-migration/v2", "manifest": manifest,
              "manifest_sha256": _hash(_wire(manifest)), "directories": directories,
              "files": [{"path": name, "size_bytes": len(raw), "sha256": _hash(raw),
                         "content_base64": base64.b64encode(raw).decode("ascii")} for name, raw in files.items()],
              "exclusions": EXCLUSIONS.copy(), "git_history_transferred": False,
              "source_git_modified": False, "execution_authorized": False}
    result["bundle_sha256"] = _hash(_wire(result))
    _bounded(result)
    if len(_wire(result)) > MAX_BUNDLE_BYTES:
        raise ValueError("knowledge bundle byte limit exceeded")
    return result


def _validated(supplied: Any) -> tuple[dict, dict[str, bytes]]:
    value = _supplied(supplied)
    keys = {"contract_version", "format", "manifest", "manifest_sha256", "files", "directories", "exclusions",
            "git_history_transferred", "source_git_modified", "execution_authorized", "bundle_sha256"}
    if (set(value) != keys or type(value["contract_version"]) is not int or value["contract_version"] != 1
            or value["format"] != "whykit.knowledge-migration/v2" or value["exclusions"] != EXCLUSIONS
            or any(value[key] is not False for key in ("git_history_transferred", "source_git_modified", "execution_authorized"))):
        raise ValueError("invalid migration bundle contract")
    if value["bundle_sha256"] != _hash(_wire({key: child for key, child in value.items() if key != "bundle_sha256"})):
        raise ValueError("migration bundle content changed")
    rows = value["files"]
    if type(rows) is not list or not rows or len(rows) > MAX_FILES:
        raise ValueError("invalid migration source inventory")
    files = {}
    total = 0
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "size_bytes", "sha256", "content_base64"}:
            raise ValueError("invalid migration source entry")
        name = _path(row["path"])
        if (name in files or type(row["size_bytes"]) is not int or not 0 <= row["size_bytes"] <= MAX_FILE_BYTES
                or type(row["content_base64"]) is not str
                or len(row["content_base64"]) > ((MAX_FILE_BYTES + 2) // 3) * 4):
            raise ValueError("invalid migration source entry")
        try:
            raw = base64.b64decode(row["content_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid migration source base64") from exc
        total += len(raw)
        if (len(raw) != row["size_bytes"] or total > MAX_RAW_BYTES or _hash(raw) != row["sha256"]
                or base64.b64encode(raw).decode("ascii") != row["content_base64"]):
            raise ValueError("migration source bytes changed or exceed limit")
        files[name] = raw
    if list(files) != sorted(files):
        raise ValueError("migration sources must be sorted")
    directories = value["directories"]
    if (type(directories) is not list or len(directories) > MAX_FILES * 4
            or any(type(name) is not str for name in directories) or directories != sorted(directories)):
        raise ValueError("invalid migration directory inventory")
    _check_inventory_paths(list(files), directories)
    manifest = _manifest(files, directories)
    if _wire(value["manifest"]) != _wire(manifest) or value["manifest_sha256"] != _hash(_wire(manifest)):
        raise ValueError("typed knowledge mapping differs from raw sources")
    _native_coverage(manifest, files)
    return value, files


def validate_migration_bundle(supplied: Any) -> dict:
    """Rebuild typed mappings from decoded bytes; self-consistent hashes are insufficient."""
    value, _ = _validated(supplied)
    return value


def _destination_namespace(value: Path) -> Path:
    path = Path(value).absolute()
    # Win32 aliases trim final dots/spaces and may address a stream after ':'.
    if any(part.split(":", 1)[0].rstrip(" .").casefold() == ".git" for part in path.parts):
        raise ValueError("migration destination cannot enter the Git metadata namespace")
    return path


def _destination(value: Path) -> Path:
    from .lint import is_vault_root
    path = _destination_namespace(value)
    if any(part in (".", "..") for part in path.parts):
        raise ValueError("restore destination must be canonical")
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise ValueError("symlinked restore destination refused")
        if parent != path and is_vault_root(parent):
            raise ValueError("restore destination cannot be inside an existing vault")
    if not path.parent.is_dir():
        raise ValueError("restore destination parent must exist")
    if path.exists():
        raise ValueError("restore destination must not exist")
    return path


def _require_native_io() -> None:
    if not _NATIVE_IO_AVAILABLE:
        raise UnsupportedMigrationPlatform("safe knowledge migration I/O requires POSIX descriptor-relative no-follow primitives")


def _open_directory(path: Path) -> int:
    _require_native_io()
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _parent_fd(root_fd: int, name: str, directories: dict[str, tuple[int, int]], *, create: bool) -> int:
    descriptor = os.dup(root_fd)
    prefix = []
    try:
        for part in name.split("/")[:-1]:
            prefix.append(part)
            relative = "/".join(prefix)
            if relative not in directories:
                if not create:
                    raise ValueError("unknown restore directory")
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
                info = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                directories[relative] = (info.st_dev, info.st_ino)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            info = os.fstat(child)
            if (info.st_dev, info.st_ino) != directories[relative]:
                os.close(child)
                raise ValueError("restore directory changed")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_entry(root_fd: int, name: str, raw: bytes, directories: dict[str, tuple[int, int]],
                 files: dict[str, tuple[int, int]]) -> None:
    descriptor = _parent_fd(root_fd, name, directories, create=True)
    try:
        file_fd = os.open(name.split("/")[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          mode=0o600, dir_fd=descriptor)
        info = os.fstat(file_fd)
        files[name] = (info.st_dev, info.st_ino)
        with os.fdopen(file_fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _cleanup_owned(root_fd: int, directories: dict[str, tuple[int, int]], files: dict[str, tuple[int, int]]) -> None:
    for name, identity in reversed(list(files.items())):
        try:
            parent = _parent_fd(root_fd, name, directories, create=False)
            try:
                leaf = name.split("/")[-1]
                info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == identity and stat.S_ISREG(info.st_mode):
                    os.unlink(leaf, dir_fd=parent)
            finally:
                os.close(parent)
        except (OSError, ValueError):
            pass
    for name, identity in reversed(list(directories.items())):
        try:
            parent = _parent_fd(root_fd, name, directories, create=False)
            try:
                leaf = name.split("/")[-1]
                info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == identity and stat.S_ISDIR(info.st_mode):
                    os.rmdir(leaf, dir_fd=parent)
            finally:
                os.close(parent)
        except (OSError, ValueError):
            pass


def restore_migration_bundle(supplied: Any, destination: Path, *, write: bool = False,
                             expected_sha256: str | None = None) -> dict:
    """Preview then create a new exact-byte tree. This operation is not globally atomic."""
    _require_native_io()
    _destination_namespace(destination)
    bundle, files = _validated(supplied)
    target = _destination(destination)
    expected = _hash(_wire({"destination": str(target), "bundle_sha256": bundle["bundle_sha256"]}))
    result = {"contract_version": 1, "format": "whykit.knowledge-restore/v2", "applied": False,
              "expected_sha256": expected, "destination": str(target), "source_files": len(files),
              "objects": len(bundle["manifest"]["objects"]), "exact_bytes_preserved": False,
              "git_history_transferred": False, "execution_authorized": False}
    if not write:
        return result
    if expected_sha256 != expected:
        raise ValueError("restore requires the current reviewed expected_sha256")
    # Atomic mkdir refuses an existing target; directory-relative writes never follow aliases.
    parent_fd = _open_directory(target.parent)
    root_fd = None
    root_identity = None
    directories: dict[str, tuple[int, int]] = {}
    owned_files: dict[str, tuple[int, int]] = {}
    try:
        os.mkdir(target.name, mode=0o700, dir_fd=parent_fd)
        root_fd = os.open(target.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(root_fd)
        root_identity = (info.st_dev, info.st_ino)
        try:
            for name in bundle["directories"]:
                descriptor = _parent_fd(root_fd, name + "/__directory_only__", directories, create=True)
                os.close(descriptor)
            for name, raw in {**files, MANIFEST_PATH: _wire(bundle["manifest"])}.items():
                _write_entry(root_fd, name, raw, directories, owned_files)
            actual, derived, actual_dirs = _inventory(target, restored=True)
            if actual != files or actual_dirs != bundle["directories"] or derived != _wire(bundle["manifest"]):
                raise ValueError("restore verification failed")
            info = target.lstat()
            if (info.st_dev, info.st_ino) != root_identity:
                raise ValueError("restore destination changed")
            os.fsync(root_fd)
            return {**result, "applied": True, "exact_bytes_preserved": True}
        except BaseException:
            _cleanup_owned(root_fd, directories, owned_files)
            try:
                info = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == root_identity and stat.S_ISDIR(info.st_mode):
                    os.rmdir(target.name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    finally:
        if root_fd is not None:
            os.close(root_fd)
        os.close(parent_fd)


_LABELS = {"public", "internal", "confidential", "restricted"}


def _capture_index(root: Path, allowed: set[str], grant: VaultIndex | None, *, full: bool):
    from .compiled_context import _impact_header_grant, _metadata, _safe_path
    from .lint import _build_index, collect_markdown, load_note
    from .sensitivity import SENSITIVITY_LEVEL, classified_evidence, note_sensitivity_level
    if grant is not None and grant.root.resolve() != root:
        raise ValueError("knowledge grant must belong to the requested root")
    paths = [note.path for note in grant.notes] if grant is not None else collect_markdown(root, [])
    preflight = None
    if not full:
        preflight = _impact_header_grant(root, paths, allowed,
                                         grant.derived.get("evidence_register") if grant is not None else None,
                                         reader=_read)
        if preflight is not None:
            paths = [note.path for note in preflight[0].notes]
    notes: list[Note] = []
    raw_by_path: dict[Path, bytes] = {}
    captured_total = 0
    levels = {SENSITIVITY_LEVEL[label] for label in allowed}
    for path in paths:
        if not _safe_path(root, path):
            if full:
                raise ValueError("unsafe knowledge source")
            continue
        try:
            if not full and note_sensitivity_level(_metadata(path)) not in levels:
                continue
            raw = preflight[1].get(path) if preflight is not None else None
            raw = _read(path) if raw is None else raw
            note = load_note(path, text=raw.decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeError):
            if full:
                raise ValueError("unreadable knowledge Markdown source") from None
            continue
        if not full and note_sensitivity_level(note) not in levels:
            continue
        captured_total += len(raw)
        if len(notes) >= MAX_FILES or captured_total > MAX_RAW_BYTES:
            raise ValueError("knowledge view input limit exceeded")
        notes.append(note)
        raw_by_path[path] = raw
    index = VaultIndex(root, notes, {note.path.resolve(): note for note in notes}, _build_index(notes), confined=True)
    inherited = grant.derived.get("evidence_register") if grant is not None else None
    if preflight is not None:
        active, retired, _ = preflight[2]
    elif inherited is None:
        active, retired, _, _ = classified_evidence(index)
    else:
        ids = set(inherited[0]) | set(inherited[1])
        active, retired = {}, {}
        if ids:
            path = root / "00-context/evidence-register.md"
            if _safe_path(root, path):
                try:
                    note = load_note(path, text=_read(path).decode("utf-8-sig"))
                    registers = VaultIndex(root, [note], {path.resolve(): note}, {}, confined=True)
                    fresh, old, _, _ = classified_evidence(registers)
                    active = {key: row for key, row in fresh.items() if key in ids}
                    retired = {key: row for key, row in old.items() if key in ids}
                except (OSError, ValueError, UnicodeError):
                    pass
    rows = {key: row for key, row in {**active, **retired}.items() if row.get("sensitivity") in allowed}
    return index, raw_by_path, rows


def _source(path: str, raw: bytes | None, *, row: bytes | None = None, occurrence: int | None = None) -> dict:
    return {"path": path, "sha256": _hash(raw) if raw is not None else None,
            "row_sha256": _hash(row) if row is not None else None, "occurrence": occurrence}


def _object(kind: str, identity: str, sensitivity: str, source: dict, data: dict) -> dict:
    return {"kind": kind, "id": identity, "sensitivity": sensitivity, "source": source, "data": data}


def _project(root: Path, allowed: set[str], grant: VaultIndex | None, *, full: bool) -> dict:
    from .artifact_store import _load_artifact
    from .claim_ledger import _parse_claim_text
    from .knowledge_visibility import canonical_decision_id, visible_knowledge_index
    from .lint import REQUIRED_KEYS
    from .outcome_ledger import _parse_text
    from .sensitivity import SENSITIVITY_LEVEL, is_claim_note, note_sensitivity_level

    index, raw, rows = _capture_index(root, allowed, grant, full=full)
    original = index
    index = visible_knowledge_index(index, set(rows))
    if full:
        # No governed records may silently vanish from a purported complete migration.
        for note in original.notes:
            governed = (is_claim_note(note) or note.front.get("type") == "outcome"
                        or "outcome_id" in note.front or "decision_id" in note.front)
            if governed and note not in index.notes:
                raise ValueError("invalid or unresolved governed knowledge record")
    objects = []
    names = {value: label for label, value in SENSITIVITY_LEVEL.items()}
    for note in index.notes:
        if note.front.get("status") == "template":
            continue
        relative = note.path.relative_to(root).as_posix()
        if is_claim_note(note):
            source = _source(relative, raw[note.path])
            record, payload = _parse_claim_text(note.text)
            objects.append(_object("Claim", record["claim_id"], "restricted", source, {"record": record, "payload": payload}))
        elif note.front.get("type") == "outcome" or "outcome_id" in note.front:
            source = _source(relative, raw[note.path])
            record, payload = _parse_text(note.text)
            objects.append(_object("Outcome", record["outcome_id"], "restricted", source, {"record": record, "payload": payload}))
        elif "decision_id" in note.front or canonical_decision_id(note, root) is not None:
            identity = canonical_decision_id(note, root)
            if (type(identity) is not str or re.fullmatch(r"D-[0-9]{3,}", identity) is None
                    or note.front.get("type") != "decision" or not set(REQUIRED_KEYS).issubset(note.front)
                    or re.fullmatch(r"06-decisions/" + identity.lower() + r"-[^/]+\.[mM][dD]", relative) is None
                    or note.front_error or note_sensitivity_level(note) not in names):
                if full:
                    raise ValueError("invalid knowledge decision record")
                continue
            source = _source(relative, raw[note.path])
            objects.append(_object("Decision", identity, names[note_sensitivity_level(note)], source,
                                   {"record": {**note.front, "decision_id": identity}, "body": note.body}))
    # Artifact projection is metadata only. Reversible bytes belong exclusively in full-scope bundles.
    base = root / "00-context/artifacts"
    from .compiled_context import _safe_path
    for evidence_id, row in sorted(rows.items()):
        directory = base / evidence_id
        if not directory.exists():
            continue
        if not _safe_path(root, directory) or not directory.is_dir():
            if full:
                raise ValueError("unsafe knowledge artifact directory")
            continue
        candidates: list[Path] = []
        for path in directory.iterdir():
            if len(candidates) >= MAX_FILES:
                raise ValueError("knowledge artifact directory limit exceeded")
            candidates.append(path)
        for path in sorted(candidates):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name) or not _safe_path(root, path):
                if full:
                    raise ValueError("noncanonical knowledge artifact path")
                continue
            version = "sha256:" + path.stem
            try:
                captured = _load_artifact(root, evidence_id, version, allowed_sensitivities=allowed,
                                           sensitivity_floor=row["sensitivity"])
            except (OSError, ValueError, PermissionError):
                if full:
                    raise ValueError("invalid knowledge artifact") from None
                continue
            if captured is None:
                continue
            envelope, _, envelope_raw = captured
            label = max((row["sensitivity"], envelope["sensitivity"]), key=SENSITIVITY_LEVEL.__getitem__)
            data = {key: envelope[key] for key in ("evidence_id", "source_version", "recorded_at", "size_bytes")}
            objects.append(_object("EvidenceArtifact", evidence_id + "@" + version, label,
                                   _source(path.relative_to(root).as_posix(), envelope_raw), data))
    # ReviewEvent visibility follows both the log label and its target's explicit grant.
    log = index.note_for(root / "00-context/review-log.md")
    if log is not None:
        from .review_history import project_review_rows
        projected, _, _ = project_review_rows(log, index, strict=full, scoped=not full)
        for event in projected:
            cells, target = event["cells"], event["target"]
            digest, occurrence = event["sha256"], event["occurrence"]
            source = _source("00-context/review-log.md", raw[log.path] if full else None,
                             row=_wire(cells), occurrence=occurrence)
            data = dict(zip(("date", "target", "reviewer", "outcome", "previous_review", "next_review", "note"), cells, strict=True))
            target_note = index.note_for(target)
            label = names.get(max(note_sensitivity_level(log), note_sensitivity_level(target_note)
                                  if target_note is not None else 0), "restricted")
            objects.append(_object("ReviewEvent", "R-" + digest + "-" + str(occurrence), label, source, data))
    objects.sort(key=lambda item: (item["kind"], item["id"]))
    if len(objects) > MAX_OBJECTS or len({(item["kind"], item["id"]) for item in objects}) != len(objects):
        raise ValueError("knowledge object limit or duplicate identity")
    result = {"contract_version": 1, "format": "whykit.knowledge/v2", "scope": "full_local" if full else "filtered",
              "objects": objects, "git_history_transferred": False, "semantics_checked": False, "execution_authorized": False}
    _bounded(result)
    if len(_wire(result)) > MAX_BUNDLE_BYTES:
        raise ValueError("knowledge view byte limit exceeded")
    return result


def project_knowledge_v2(root: Path, *, allowed_sensitivities: set[str] | None = None,
                         vault: VaultIndex | None = None) -> dict:
    """Default-public additive typed view. A supplied index can only narrow its grant."""
    if allowed_sensitivities is not None and (type(allowed_sensitivities) is not set or not allowed_sensitivities <= _LABELS):
        raise ValueError("invalid knowledge sensitivity set")
    allowed = {"public"} if allowed_sensitivities is None else allowed_sensitivities
    root = Path(root).resolve(strict=True)
    with vault_read_lock(root), reuse_notes(NoteCache()):
        return _project(root, allowed, vault, full=False)


def export_migration_bundle(root: Path, output: Path, *, full_local_scope: bool = False,
                            write: bool = False, expected_sha256: str | None = None) -> dict:
    """Preview then atomically create a new portable JSON file outside the source vault.

    POSIX-only publication uses a private file plus create-only hard link. It
    never replaces an existing file and never changes the source vault or Git.
    """
    _require_native_io()
    target = _destination_namespace(output)
    root = Path(root).resolve(strict=True)
    if any(part in (".", "..") for part in target.parts) or target.is_relative_to(root):
        raise ValueError("migration output must be canonical and outside the source vault")
    for component in [*reversed(target.parents), target]:
        if component.is_symlink():
            raise ValueError("symlinked migration output refused")
    if not target.parent.is_dir() or target.exists():
        raise ValueError("migration output must not exist and its parent must exist")
    with vault_read_lock(root), reuse_notes(NoteCache()):
        bundle = build_migration_bundle(root, full_local_scope=full_local_scope)
        raw = _wire(bundle)
        expected = _hash(_wire({"root": str(root), "output": str(target), "target": None,
                               "bundle_sha256": bundle["bundle_sha256"]}))
        result = {"contract_version": 1, "format": "whykit.knowledge-export/v2", "applied": False,
                  "expected_sha256": expected, "output": str(target), "source_files": len(bundle["files"]),
                  "objects": len(bundle["manifest"]["objects"]), "bundle_sha256": bundle["bundle_sha256"],
                  "git_history_transferred": False, "source_git_modified": False, "execution_authorized": False}
        if not write:
            return result
        if expected_sha256 != expected:
            raise ValueError("export requires the current reviewed expected_sha256")
        parent_fd = _open_directory(target.parent)
        temporary = ".whykit-export-" + secrets.token_hex(16) + ".tmp"
        identity = None
        published = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode=0o600, dir_fd=parent_fd)
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
                os.link(temporary, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                published = True
                current = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                parent = target.parent.lstat()
                opened_parent = os.fstat(parent_fd)
                if ((current.st_dev, current.st_ino) != identity or not stat.S_ISREG(current.st_mode)
                        or (parent.st_dev, parent.st_ino) != (opened_parent.st_dev, opened_parent.st_ino)
                        or _read_at(parent_fd, target.name, limit=MAX_BUNDLE_BYTES) != raw):
                    raise ValueError("migration output changed during publication")
                os.fsync(parent_fd)
            return {**result, "applied": True}
        except BaseException:
            if published and identity is not None:
                _unlink_export(parent_fd, target.name, identity)
            raise
        finally:
            if identity is not None:
                _unlink_export(parent_fd, temporary, identity)
            os.close(parent_fd)


def _unlink_export(parent_fd: int, name: str, identity: tuple[int, int]) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
            os.unlink(name, dir_fd=parent_fd)
    except OSError:
        pass
