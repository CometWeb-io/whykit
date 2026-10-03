"""Small durability helpers for governed WhyKit writes."""
from __future__ import annotations

import errno
import hashlib
import codecs
import json
import os
import secrets
import stat
import time
from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from pathlib import Path


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


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace *path* atomically after flushing the complete new contents.

    The temporary file is created beside the target so ``os.replace`` stays on
    the same filesystem. Existing permission bits are preserved. The helper is
    intentionally dependency-free and leaves the original file untouched if
    writing or fsyncing the replacement fails.
    """
    path = Path(path)
    ensure_writable(path)
    text = match_line_endings(path, text)
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
        with tmp.open("x", encoding=encoding, errors=errors, newline="") as handle:
            handle.write(text)
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
        try:
            fd = os.open(path.parent, os.O_RDONLY)
        except (OSError, AttributeError):
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


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
        try:
            fd = os.open(path.parent, os.O_RDONLY)
        except (OSError, AttributeError):
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
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


@contextmanager
def vault_mutation_lock(root: Path, *, timeout: float = 15.0) -> Iterator[None]:
    """Serialize ID allocation and multi-file ledger mutations for one vault.

    Uses an OS advisory exclusive lock on ``.whykit/mutation.lock``. The kernel
    releases the lock when the process dies, so crash/SIGKILL cannot leave a
    permanent directory lock that blocks all future mutations.
    """
    root = Path(root).resolve(strict=True)
    whykit_dir = safe_vault_dir(root, ".whykit")
    lock_path = whykit_dir / "mutation.lock"
    handle = lock_path.open("a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _acquire_exclusive(handle, non_blocking=True)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"vault is already being modified: {root}") from None
                time.sleep(0.05)
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
        resolved_target = Path(target).resolve()
        try:
            relative = resolved_target.relative_to(root).as_posix()
        except ValueError as exc:
            raise RuntimeError(f"transaction target escapes vault: {target}") from exc
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
    """Apply a READY transaction, then mark it COMMITTED."""
    root = Path(root).resolve(strict=True)
    tx = Path(tx)
    ready = tx / "READY"
    committed = tx / "COMMITTED"
    if committed.exists():
        return
    if not ready.exists():
        raise RuntimeError(f"transaction is not READY: {tx}")
    manifest = json.loads((tx / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise RuntimeError(f"malformed transaction manifest: {tx}")
    for item in manifest:
        if not isinstance(item, dict):
            raise RuntimeError(f"malformed transaction entry: {tx}")
        source = tx / str(item["staged"])
        target = safe_vault_target(root, str(item["target"]))
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != item.get("sha256"):
            raise RuntimeError(f"staged content digest mismatch: {source}")
        atomic_write_bytes(target, data)
        source.unlink(missing_ok=True)
    atomic_write_text(committed, "1\n")


def recover_pending_transactions(root: Path) -> list[Path]:
    """Finish READY transactions that never reached COMMITTED (crash recovery)."""
    root = Path(root).resolve(strict=True)
    base = root / ".whykit" / "transactions"
    if not base.is_dir():
        return []
    recovered: list[Path] = []
    for tx in sorted(base.iterdir()):
        if not tx.is_dir():
            continue
        if (tx / "READY").exists() and not (tx / "COMMITTED").exists():
            commit_transaction(root, tx)
            recovered.append(tx)
    return recovered


def apply_transaction(root: Path, updates: Mapping[Path, str]) -> None:
    """Stage then commit a multi-file update under the caller's mutation lock."""
    tx = stage_transaction(root, updates)
    commit_transaction(root, tx)
