"""Small durability helpers for governed WhyKit writes."""
from __future__ import annotations

import errno
import contextvars
import asyncio
import threading
from dataclasses import dataclass
import functools
import shutil
import hashlib
import codecs
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TextIO


def _sync_directory(path: Path) -> None:
    """Persist a rename on systems that support syncing directories."""
    if os.name == "nt":
        return  # Windows does not expose directory fsync through os.open.
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(fd)


def ensure_writable(path: Path) -> None:
    """Refuse to replace a file someone marked read-only.

    ``os.replace`` would silently override the mark on POSIX, while Windows
    refuses the rename and then cannot delete the read-only temporary file
    either. Checking first gives one answer on every platform, before any
    byte of a multi-file change is written.
    """
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISREG(mode) and (not mode & stat.S_IWUSR or not os.access(path, os.W_OK)):
        raise PermissionError(errno.EACCES, "file is read-only; make it writable before WhyKit updates it", str(path))


def match_line_endings(path: Path, text: str) -> str:
    """Keep an existing CRLF file CRLF when WhyKit rewrites it.

    Edits read text with universal newlines, so writing it back as-is would
    turn every line of a Windows-authored file into a change. A new file, or
    one whose first line ends in LF, is written with LF.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(65536)
    except OSError:
        return text
    first = head.find(b"\n")
    if first <= 0 or head[first - 1:first] != b"\r":
        return text
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


@contextmanager
def _atomic_text_writer(path: Path, *, encoding: str = "utf-8", newline: str = "") -> Iterator[TextIO]:
    """Share the same durable replacement for plain text and streamed JSON."""
    path = Path(path)
    ensure_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.whykit-tmp-{secrets.token_hex(6)}")
    previous_mode = None
    try:
        try:
            previous_mode = path.stat().st_mode & 0o7777
        except FileNotFoundError:
            pass
        # surrogateescape: under a C/ASCII locale, command-line text (a title,
        # an owner) arrives with surrogates for its UTF-8 bytes; write those
        # bytes back instead of refusing the record.
        errors = "surrogateescape" if codecs.lookup(encoding).name == "utf-8" else "strict"
        with tmp.open("x", encoding=encoding, errors=errors, newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        if previous_mode is not None:
            os.chmod(tmp, previous_mode)
        os.replace(tmp, path)
        _sync_directory(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace atomically after fsync, preserving mode and existing CRLF."""
    with _atomic_text_writer(path, encoding=encoding) as handle:
        handle.write(match_line_endings(path, text))


def atomic_write_json(path: Path, value: object) -> None:
    """Stream pretty UTF-8 JSON into the same fsync-backed atomic replacement."""
    newline = match_line_endings(path, "\n")
    with _atomic_text_writer(path, newline=newline) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Binary counterpart of :func:`atomic_write_text`."""
    path = Path(path)
    ensure_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.whykit-tmp-{secrets.token_hex(6)}")
    previous_mode = None
    try:
        try:
            previous_mode = path.stat().st_mode & 0o7777
        except FileNotFoundError:
            pass
        with tmp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        if previous_mode is not None:
            os.chmod(tmp, previous_mode)
        os.replace(tmp, path)
        _sync_directory(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def safe_vault_dir(root: Path, relative: str | Path) -> Path:
    """Return a vault-relative directory, creating missing parents without following symlinks."""
    root = Path(root).resolve(strict=True)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe vault path: {relative}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"symlinked vault directory refused: {current}")
        if not current.exists():
            try:
                current.mkdir(mode=0o755)
                _sync_directory(current.parent)
            except FileExistsError:
                # Another writer may have created this directory after our
                # existence check. Revalidate below; in particular, do not
                # accept a symlink created in the same race.
                pass
        if current.is_symlink():
            raise RuntimeError(f"symlinked vault directory refused: {current}")
        if not current.is_dir():
            raise RuntimeError(f"expected directory: {current}")
        resolved = current.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"path escapes vault: {relative}") from exc
    return current


def safe_vault_target(root: Path, relative: str | Path, *, create_parents: bool = True) -> Path:
    """Resolve a vault-relative write path without following parent symlinks."""
    root = Path(root).resolve(strict=True)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe vault path: {relative}")

    parts = relative.parts
    if not parts:
        raise ValueError("unsafe vault path: empty")

    if len(parts) > 1:
        if create_parents:
            safe_vault_dir(root, Path(*parts[:-1]))
        else:
            parent = root.joinpath(*parts[:-1])
            if parent.is_symlink():
                raise RuntimeError(f"symlinked vault directory refused: {parent}")
            parent.resolve(strict=True).relative_to(root)

    target = root / relative
    if target.is_symlink():
        raise RuntimeError(f"refusing to mutate symlink: {target}")
    parent = target.parent.resolve(strict=True)
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"path escapes vault: {relative}") from exc
    return target



def safe_export_target(root: Path, relative: str | Path) -> Path:
    """Exports must not replace Git state or the inode that coordinates readers."""
    parts = tuple(part.split(":", 1)[0].rstrip(" .").casefold() for part in Path(relative).parts)
    if parts and (parts[0] == ".git" or parts[:2] in {
        (".whykit", "mutation.lock"), (".whykit", "transactions"), (".whykit", "cache"),
    }):
        raise ValueError("export cannot replace Git or WhyKit coordination state")
    return safe_vault_target(root, relative)

def _acquire_exclusive(handle, *, non_blocking: bool) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows runners
        import msvcrt

        handle.seek(0)
        mode = msvcrt.LK_NBLCK if non_blocking else msvcrt.LK_LOCK
        msvcrt.locking(handle.fileno(), mode, 1)
        return
    import fcntl

    flags = fcntl.LOCK_EX
    if non_blocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(handle.fileno(), flags)


def _release_exclusive(handle) -> None:
    if os.name == "nt":  # pragma: no cover
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class _HeldLock:
    mode: str
    owner: tuple[int, int, int | None]
    active: bool = True


def _lock_owner() -> tuple[int, int, int | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), id(task) if task is not None else None


_HELD_LOCKS: contextvars.ContextVar[dict[Path, _HeldLock] | None] = contextvars.ContextVar("whykit_vault_locks", default=None)


def _owned_lock(held: dict[Path, _HeldLock], root: Path) -> _HeldLock | None:
    lock = held.get(root)
    return lock if lock is not None and lock.active and lock.owner == _lock_owner() else None


def _acquire_shared(handle) -> None:
    if os.name == "nt":  # pragma: no cover - Windows CI
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBRLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)


def _pending_transactions(root: Path) -> bool:
    base = root / ".whykit" / "transactions"
    if base.is_symlink():
        raise OSError(errno.EBUSY, "symlinked transaction state refused")
    if not base.exists():
        return False
    for tx in base.iterdir():
        if tx.is_symlink():
            raise OSError(errno.EBUSY, "symlinked transaction state refused")
        if (tx / "READY").exists() and not (tx / "COMMITTED").exists():
            return True
    return False


@contextmanager
def vault_read_lock(root: Path, *, timeout: float = 15.0) -> Iterator[None]:
    """Hold a shared OS lock without writing to the vault; pending recovery fails closed.

    A pristine vault has no lock file. Detect its creation before returning a
    result, so its first writer cannot race the reader. External editors that
    ignore the advisory lock are outside this transaction guarantee.
    """
    root = Path(root).resolve(strict=True)
    held = _HELD_LOCKS.get() or {}
    if _owned_lock(held, root) is not None:
        yield
        return
    state = root / ".whykit"
    lock_path = state / "mutation.lock"
    if state.is_symlink() or lock_path.is_symlink():
        raise OSError(errno.EBUSY, "symlinked mutation state refused")
    try:
        handle = lock_path.open("rb")
    except FileNotFoundError:
        handle = None
    token = None
    ownership = _HeldLock("read", _lock_owner())
    try:
        if handle is not None:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _acquire_shared(handle)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise OSError(errno.EBUSY, "vault write in progress; retry the read") from None
                    time.sleep(0.01)
        if _pending_transactions(root):
            raise OSError(errno.EBUSY, "vault recovery required; run whykit recover before reading")
        token = _HELD_LOCKS.set({**held, root: ownership})
        yield
        if handle is None and (lock_path.exists() or _pending_transactions(root)):
            raise OSError(errno.EBUSY, "vault changed during its first governed write; retry the read")
    finally:
        ownership.active = False
        if token is not None:
            _HELD_LOCKS.reset(token)
        if handle is not None:
            try:
                _release_exclusive(handle)
            finally:
                handle.close()


def consistent_read(handler):
    """Protect a complete root-first report, including its nested config/source reads."""
    @functools.wraps(handler)
    def guarded(root, *args, **kwargs):
        with vault_read_lock(root):
            return handler(root, *args, **kwargs)
    return guarded


@contextmanager
def vault_mutation_lock(root: Path, *, timeout: float = 15.0) -> Iterator[None]:
    """Serialize ID allocation and multi-file ledger mutations for one vault.

    Uses an OS advisory exclusive lock on ``.whykit/mutation.lock``. The kernel
    releases the lock when the process dies, so crash/SIGKILL cannot leave a
    permanent directory lock that blocks all future mutations.
    """
    root = Path(root).resolve(strict=True)
    held = _HELD_LOCKS.get() or {}
    current = _owned_lock(held, root)
    if current is not None:
        if current.mode != "write":
            raise RuntimeError("cannot upgrade a vault read lock to a write lock")
        yield
        return
    lock_path = safe_vault_target(root, ".whykit/mutation.lock")
    handle = lock_path.open("a+b")
    deadline = time.monotonic() + timeout
    token = None
    ownership = _HeldLock("write", _lock_owner())
    try:
        while True:
            try:
                _acquire_exclusive(handle, non_blocking=True)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"vault is already being modified: {root}") from None
                time.sleep(0.05)
        token = _HELD_LOCKS.set({**held, root: ownership})
        # Crash recovery must run under the lock so two agents never finish the
        # same READY journal concurrently.
        recover_pending_transactions(root)
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps({"pid": os.getpid(), "started_at": time.time()}, ensure_ascii=False).encode("utf-8")
        )
        handle.flush()
        yield
    finally:
        ownership.active = False
        if token is not None:
            _HELD_LOCKS.reset(token)
        try:
            _release_exclusive(handle)
        except OSError:
            pass
        handle.close()


def stage_transaction(root: Path, updates: Mapping[Path, str]) -> Path:
    """Stage a multi-file write batch under ``.whykit/transactions/<id>/``."""
    root = Path(root).resolve(strict=True)
    if not updates:
        raise ValueError("transaction requires at least one update")
    for target in updates:
        ensure_writable(Path(target))
    txid = secrets.token_hex(12)
    tx = safe_vault_dir(root, Path(".whykit") / "transactions" / txid)
    manifest: list[dict[str, str]] = []
    for index, (target, content) in enumerate(updates.items()):
        if Path(target).is_symlink():
            raise RuntimeError(f"refusing to mutate symlink: {target}")
        resolved_target = Path(target).resolve()
        try:
            relative = resolved_target.relative_to(root).as_posix()
        except ValueError as exc:
            raise RuntimeError(f"transaction target escapes vault: {target}") from exc
        if resolved_target.is_relative_to(root / ".whykit"):
            raise ValueError(f"transaction cannot replace its own state: {target}")
        staged_name = f"{index}.new"
        staged = tx / staged_name
        content = match_line_endings(resolved_target, content)
        atomic_write_text(staged, content)
        manifest.append({
            "target": relative,
            "staged": staged_name,
            "sha256": hashlib.sha256(content.encode("utf-8", "surrogateescape")).hexdigest(),
        })
    # ASCII JSON: paths decoded under a C locale carry surrogates, which UTF-8
    # cannot encode; json escapes round-trip them exactly.
    atomic_write_text(tx / "manifest.json", json.dumps(manifest, sort_keys=True, ensure_ascii=True))
    atomic_write_text(tx / "READY", "1\n")
    return tx


def commit_transaction(root: Path, tx: Path) -> None:
    """Replay a READY transaction; discard staged bytes only after COMMITTED."""
    root = Path(root).resolve(strict=True)
    tx = Path(tx)
    base = safe_vault_dir(root, ".whykit/transactions")
    if tx.parent.resolve() != base or tx.is_symlink():
        raise RuntimeError(f"unsafe transaction directory: {tx}")
    ready = tx / "READY"
    committed = tx / "COMMITTED"
    if not ready.exists():
        raise RuntimeError(f"transaction is not READY: {tx}")
    if any(path.is_symlink() for path in (ready, committed, tx / "manifest.json")):
        raise RuntimeError(f"symlinked transaction metadata refused: {tx}")
    manifest = json.loads((tx / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        raise RuntimeError(f"malformed transaction manifest: {tx}")
    entries: list[tuple[Path, Path, str]] = []
    sources: set[str] = set()
    targets: set[Path] = set()
    for item in manifest:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("target"), str)
            or not isinstance(item.get("staged"), str)
            or not re.fullmatch(r"[0-9]+\.new", item["staged"])
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise RuntimeError(f"malformed transaction entry: {tx}")
        source = tx / item["staged"]
        target = safe_vault_target(root, item["target"])
        if source.is_symlink() or item["staged"] in sources or target in targets:
            raise RuntimeError(f"unsafe or duplicate transaction entry: {tx}")
        if target.is_relative_to(root / ".whykit"):
            raise RuntimeError(f"transaction cannot replace its own state: {target}")
        sources.add(item["staged"])
        targets.add(target)
        entries.append((source, target, item["sha256"]))
    if not committed.exists():
        for source, target, expected in entries:
            try:
                data = source.read_bytes()
            except FileNotFoundError:
                # Older journals deleted each source immediately after writing.
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
                    continue
                raise RuntimeError(f"cannot recover missing staged content: {source}; restore it from a backup") from None
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError(f"staged content digest mismatch: {source}")
            atomic_write_bytes(target, data)
        atomic_write_text(committed, "1\n")
    _discard_transaction(tx)


def _discard_transaction(tx: Path) -> None:
    # Rename before unlinking: a crash during deletion must never replay targets.
    garbage = tx.with_name("gc-" + tx.name)
    os.replace(tx, garbage)
    _sync_directory(garbage.parent)
    shutil.rmtree(garbage)
    _sync_directory(garbage.parent)


def recover_pending_transactions(root: Path) -> list[Path]:
    """Finish READY transactions that never reached COMMITTED (crash recovery)."""
    root = Path(root).resolve(strict=True)
    base = root / ".whykit" / "transactions"
    if not base.is_dir():
        return []
    base = safe_vault_dir(root, ".whykit/transactions")
    recovered: list[Path] = []
    for tx in sorted(base.iterdir()):
        if tx.is_symlink():
            raise RuntimeError(f"symlinked transaction directory refused: {tx}")
        if not tx.is_dir():
            continue
        if re.fullmatch(r"gc-[0-9a-f]{24}", tx.name):
            shutil.rmtree(tx)
            _sync_directory(base)
        elif (tx / "READY").exists():
            pending = not (tx / "COMMITTED").exists()
            commit_transaction(root, tx)
            if pending:
                recovered.append(tx)
        elif re.fullmatch(r"[0-9a-f]{24}", tx.name) and time.time() - tx.stat().st_mtime >= 86400:
            # Pre-READY journals never touched targets; retain young ones for inspection.
            _discard_transaction(tx)
    return recovered


def apply_transaction(root: Path, updates: Mapping[Path, str]) -> None:
    """Stage then commit a multi-file update under the caller's mutation lock."""
    tx = stage_transaction(root, updates)
    commit_transaction(root, tx)
