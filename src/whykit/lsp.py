"""A read-only Language Server for WhyKit vaults (``whykit lsp``).

Speaks JSON-RPC 2.0 over stdio with the subset of LSP 3.17 an editor needs to
show lint findings and navigate a vault:

* ``textDocument/publishDiagnostics`` from the same linter as ``whykit lint``,
  run on open, change, save and watched-file events, debounced per vault;
* completion for ``[[wikilinks]]`` (paths, aliases, decision IDs) and for
  ``E-NNN`` / ``D-NNN`` identifiers;
* hover and go-to-definition for wikilinks and identifiers;
* document links for wikilinks and local Markdown links.

The server never writes a file.  Open documents are linted from the editor's
buffer, so unsaved edits are checked as typed; everything else is read from
disk.  The evidence register and ``whykit.toml`` are always read from disk by
the linter, so their findings follow the saved file.

Standard library only, like the rest of the core package.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys
import threading
import traceback
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO, Any
from urllib.parse import quote, quote_from_bytes, unquote, unquote_to_bytes, urlsplit

from . import __version__
from .lint import (
    CONTENT_SKIP_DIRS,
    DECISION_FILE_RE,
    MARKDOWN_LINK_RE,
    URL_SCHEME_RE,
    WIKILINK_RE,
    Finding,
    Note,
    _build_index,
    _decision_own_id,
    _mask_code,
    _REQUEST,
    _parse_evidence_register_text,
    _real,
    _RequestCache,
    _within,
    collect_markdown,
    evidence_register,
    is_vault_root,
    find_vault_root,
    lint,
    load_note,
    path_cache,
)
from .vault_index import NoteCache, VaultIndex

JsonObject = dict[str, Any]
Send = Callable[[JsonObject], None]

# JSON-RPC and LSP error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_NOT_INITIALIZED = -32002

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
COMPLETION_LIMIT = 200
ENCODINGS = ("utf-16", "utf-8", "utf-32")
REGISTER = PurePosixPath("00-context/evidence-register.md")

ID_RE = re.compile(r"\b[ED]-[0-9]{3,}\b")
ID_PREFIX_RE = re.compile(r"(?<![\w-])([ED]-[0-9]*)$")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
MESSAGE_LINK_RE = re.compile(r"\[\[(.+?)\]\]")
MESSAGE_PAREN_RE = re.compile(r"\(([^()]+)\)$")

# Findings that depend on files other than the one they are reported on.  A
# lint of only the edited files cannot produce them, so while typing they are
# carried over from the last whole-vault lint.  Keep in step with the checks
# `lint()` runs only without `paths` (and with decision.duplicate, which needs
# the other holder of the ID); tests/test_lsp.py holds the two together.
CROSS_FILE_CODES = frozenset({
    "agents.absent", "agents.unconfigured", "config.invalid", "decision.duplicate", "hub.unlinked_workstream",
    "note.orphan", "path.case_collision", "review_log.date", "review_log.next_review",
    "review_log.outcome", "review_log.reviewer", "review_log.row", "review_log.table",
    "review_log.target", "secret.detected", "secret.scan_skipped_large_file", "secret.scan_unreadable",
})

# LSP enums.
SEVERITY = {"error": 1, "warning": 2, "info": 3, "information": 3, "hint": 4}
KIND_FILE, KIND_REFERENCE, KIND_CONSTANT = 17, 18, 21
TAG_DEPRECATED = 1


class ProtocolError(ValueError):
    """The byte stream is not a valid base-protocol frame."""


class _RequestError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Base protocol: Content-Length framed JSON over a byte stream.


def read_message(stream: IO[bytes]) -> JsonObject | None:
    """Read one framed message; ``None`` at end of input.

    Raises :class:`ProtocolError` for a header block without a usable
    ``Content-Length`` and :class:`json.JSONDecodeError` for a body that is not
    JSON (the frame has been consumed, so the caller can answer and go on).
    """
    length: int | None = None
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            if length is None:
                raise ProtocolError("message header has no Content-Length")
            break
        name, _, value = line.decode("ascii", "replace").partition(":")
        if name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError as exc:
                raise ProtocolError(f"bad Content-Length: {value.strip()!r}") from exc
            if length < 0:
                raise ProtocolError("negative Content-Length")
    assert length is not None
    if length > MAX_MESSAGE_BYTES:
        remaining = length
        while remaining > 0:
            chunk = stream.read(min(remaining, 1 << 20))
            if not chunk:
                return None
            remaining -= len(chunk)
        raise ProtocolError(f"message of {length} bytes is over the {MAX_MESSAGE_BYTES}-byte limit")
    body = stream.read(length)
    if len(body) < length:
        return None
    message = json.loads(body.decode("utf-8"))
    if not isinstance(message, dict):
        raise json.JSONDecodeError("a message must be a JSON object", body.decode("utf-8", "replace"), 0)
    return message


def write_message(stream: IO[bytes], message: JsonObject) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
    stream.flush()


# --------------------------------------------------------------------------
# URIs and positions.


def uri_to_path(uri: str, *, windows: bool | None = None) -> str:
    """The filesystem path of a ``file:`` URI.

    Accepts both drive spellings editors send (``file:///c%3A/...`` from VS
    Code, ``file:///C:/...`` elsewhere) and UNC shares (``file://host/share``).
    """
    parts = urlsplit(uri)
    if parts.scheme.lower() != "file":
        raise ValueError(f"not a file URI: {uri}")
    windows = os.name == "nt" if windows is None else windows
    if windows:
        path = unquote(parts.path)
        if parts.netloc and parts.netloc.lower() != "localhost":
            return str(PureWindowsPath(f"//{parts.netloc}{path}"))
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return str(PureWindowsPath(path))
    return os.fsdecode(unquote_to_bytes(parts.path))


def path_to_uri(path: str | os.PathLike[str], *, windows: bool | None = None) -> str:
    """A ``file:`` URI for an absolute path, percent-encoding everything but ``/``."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        pure = PureWindowsPath(path)
        drive = pure.drive
        rest = pure.as_posix()[len(drive):]
        if drive.startswith(("\\\\", "//")):
            host, _, share = drive.replace("\\", "/").lstrip("/").partition("/")
            return f"file://{host}/{quote(share)}{quote(rest)}"
        return f"file:///{drive}{quote(rest)}"
    text = os.fspath(PurePosixPath(os.fspath(path)))
    return "file://" + quote_from_bytes(os.fsencode(text), safe="/")


def _units(char: str, encoding: str) -> int:
    if encoding == "utf-16":
        return 2 if ord(char) > 0xFFFF else 1
    if encoding == "utf-8":
        return len(char.encode("utf-8", "surrogatepass"))
    return 1


def to_character(line: str, index: int, encoding: str) -> int:
    """LSP ``character`` of string index *index* in *line*."""
    index = max(0, min(index, len(line)))
    if encoding == "utf-32":
        return index
    return sum(_units(char, encoding) for char in line[:index])


def from_character(line: str, character: int, encoding: str) -> int:
    """String index of LSP ``character`` in *line*, clamped to the line."""
    if encoding == "utf-32":
        return max(0, min(character, len(line)))
    units = 0
    for index, char in enumerate(line):
        if units >= character:
            return index
        units += _units(char, encoding)
    return len(line)


def _split_lines(text: str) -> list[str]:
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def _offset_at(text: str, position: JsonObject, encoding: str) -> int:
    line_no = int(position.get("line", 0))
    start = 0
    for _ in range(max(0, line_no)):
        found = text.find("\n", start)
        if found < 0:
            return len(text)
        start = found + 1
    end = text.find("\n", start)
    end = len(text) if end < 0 else end
    line = text[start:end].removesuffix("\r")
    return start + from_character(line, int(position.get("character", 0)), encoding)


def apply_change(text: str, change: JsonObject, encoding: str) -> str:
    """Apply one ``contentChanges`` entry (full or ranged) to *text*."""
    new_text = str(change.get("text", ""))
    span = change.get("range")
    if not isinstance(span, dict):
        return new_text
    start = _offset_at(text, span.get("start", {}), encoding)
    end = _offset_at(text, span.get("end", {}), encoding)
    if end < start:
        start, end = end, start
    return text[:start] + new_text + text[end:]


def _key(path: Path) -> str:
    """Identity of a file across spellings: real path, case-folded where the OS is, NFC."""
    return unicodedata.normalize("NFC", os.path.normcase(os.fspath(_real(path))))


# --------------------------------------------------------------------------
# Server state.


class _Document:
    __slots__ = ("uri", "path", "text", "version", "vault", "_masked")

    def __init__(self, uri: str, path: Path | None, text: str, version: int | None, vault: _Vault | None) -> None:
        self.uri = uri
        self.path = path
        self.text = text
        self.version = version
        self.vault = vault
        self._masked: tuple[str, list[str], list[str]] | None = None

    def lines(self) -> tuple[list[str], list[str]]:
        """Raw and code-masked lines of the current text."""
        if self._masked is None or self._masked[0] is not self.text:
            normalized = self.text.replace("\r\n", "\n")
            self._masked = (self.text, _split_lines(normalized), _split_lines(_mask_code(normalized)))
        return self._masked[1], self._masked[2]


class _Vault:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.notes = NoteCache()
        # The last scan of the disk: parsed notes, their real paths and keys.
        self.disk: list[Note] | None = None
        self.disk_by_path: dict[Path, Note] = {}
        self.disk_keys: dict[str, int] = {}
        # Disk plus open buffers, as of the last lint; navigation reads it.
        self.index: VaultIndex | None = None
        self.decisions: dict[str, Note] | None = None
        # Findings of the last whole-vault lint, by file key.
        self.full_findings: dict[str, list[Finding]] | None = None
        self.published: set[str] = set()
        self.full_pending = False
        self.changed: set[str] = set()
        self.timer: threading.Timer | None = None
        self.lint_lock = threading.Lock()
        # Resolved paths, shared by every lint and request until the next
        # whole-vault lint rescans the disk and starts a new one.
        self.paths = _RequestCache()


@contextlib.contextmanager
def _paths(vault: _Vault | None) -> Iterator[None]:
    """Resolve paths through *vault*'s long-lived cache (a fresh one without a vault)."""
    if vault is None:
        with path_cache():
            yield
        return
    token = _REQUEST.set(vault.paths)
    try:
        yield
    finally:
        _REQUEST.reset(token)


class LanguageServer:
    """Protocol handler.  Feed it decoded messages; it calls *send* with replies.

    *debounce* is in seconds.  With ``0`` the lint runs inline, before
    :meth:`handle` returns, which keeps tests deterministic.
    """

    def __init__(
        self,
        send: Send,
        *,
        root: Path | None = None,
        debounce: float = 0.3,
        today: dt.date | None = None,
    ) -> None:
        self._send = send
        self.root = Path(root).resolve() if root is not None else None
        self.debounce = max(0.0, debounce)
        self.today = today
        self.encoding = "utf-16"
        self.running = True
        self.exit_code: int | None = None
        self.lint_runs = 0
        self._initialized = False
        self._shutdown = False
        self._documents: dict[str, _Document] = {}
        self._vaults: dict[Path, _Vault] = {}
        self._vault_dirs: dict[str, _Vault | None] = {}
        self._workspace: list[_Vault] = []
        self._lock = threading.RLock()
        self._watch_registration = False

    # ---------------------------------------------------------------- plumbing

    def send(self, message: JsonObject) -> None:
        self._send({"jsonrpc": "2.0", **message})

    def notify(self, method: str, params: JsonObject) -> None:
        self.send({"method": method, "params": params})

    def log(self, text: str, level: int = 3) -> None:
        self.notify("window/logMessage", {"type": level, "message": text})

    def handle(self, message: JsonObject) -> None:
        method = message.get("method")
        ident = message.get("id")
        is_request = "id" in message
        if not isinstance(method, str):
            if is_request and ("result" in message or "error" in message):
                return  # a reply to one of our requests (capability registration)
            self.send({"id": ident if is_request else None, "error": {"code": INVALID_REQUEST, "message": "missing method"}})
            return
        params = message.get("params", {})
        try:
            result = self._dispatch(method, params if params is not None else {}, is_request)
        except _RequestError as exc:
            if is_request:
                self.send({"id": ident, "error": {"code": exc.code, "message": str(exc)}})
            return
        except Exception as exc:  # noqa: BLE001 - one bad message must not end the session
            if os.environ.get("WHYKIT_DEBUG"):
                traceback.print_exc(file=sys.stderr)
            text = f"whykit lsp: {method} failed: {type(exc).__name__}: {exc}"
            if is_request:
                self.send({"id": ident, "error": {"code": INTERNAL_ERROR, "message": text}})
            else:
                self.log(text, 1)
            return
        if is_request:
            self.send({"id": ident, "result": result})

    def _dispatch(self, method: str, params: Any, is_request: bool) -> Any:
        if method == "exit":
            self._stop()
            return None
        if method == "initialize":
            return self._initialize(params)
        if not self._initialized:
            if is_request:
                raise _RequestError(SERVER_NOT_INITIALIZED, "server not initialized")
            return None
        if self._shutdown and is_request:
            raise _RequestError(INVALID_REQUEST, "server is shutting down")
        handler = _HANDLERS.get(method)
        if handler is None:
            if is_request and not method.startswith("$/"):
                raise _RequestError(METHOD_NOT_FOUND, f"method not supported: {method}")
            return None
        if not isinstance(params, dict):
            raise _RequestError(INVALID_PARAMS, "params must be an object")
        if not is_request:
            return handler(self, params)
        with _paths(self._request_vault(params)):
            return handler(self, params)

    def _request_vault(self, params: JsonObject) -> _Vault | None:
        ident = params.get("textDocument")
        uri = ident.get("uri") if isinstance(ident, dict) else None
        if not isinstance(uri, str):
            return None
        with self._lock:
            doc = self._documents.get(uri)
            if doc is not None:
                return doc.vault
            path = self._path(uri)
            return self._vault_containing(path) if path is not None else None

    def _stop(self) -> None:
        with self._lock:
            for vault in self._vaults.values():
                if vault.timer is not None:
                    vault.timer.cancel()
        self.running = False
        self.exit_code = 0 if self._shutdown else 1

    # --------------------------------------------------------------- lifecycle

    def _initialize(self, params: Any) -> JsonObject:
        if not isinstance(params, dict):
            raise _RequestError(INVALID_PARAMS, "params must be an object")
        capabilities = params.get("capabilities") or {}
        offered = ((capabilities.get("general") or {}).get("positionEncodings")) or []
        self.encoding = next((enc for enc in offered if enc in ENCODINGS), "utf-16")
        watched = ((capabilities.get("workspace") or {}).get("didChangeWatchedFiles")) or {}
        self._watch_registration = bool(watched.get("dynamicRegistration"))
        folders: list[str] = []
        for folder in params.get("workspaceFolders") or []:
            if isinstance(folder, dict) and isinstance(folder.get("uri"), str):
                folders.append(folder["uri"])
        if isinstance(params.get("rootUri"), str):
            folders.append(params["rootUri"])
        elif isinstance(params.get("rootPath"), str):
            folders.append(path_to_uri(params["rootPath"]))
        with self._lock:
            if self.root is not None:
                self._workspace = [self._vault_at(self.root)]
            else:
                for uri in folders:
                    path = self._path(uri)
                    vault = self._vault_containing(path) if path is not None else None
                    if vault is not None and vault not in self._workspace:
                        self._workspace.append(vault)
        self._initialized = True
        return {
            "capabilities": {
                "positionEncoding": self.encoding,
                "textDocumentSync": {"openClose": True, "change": 2, "save": {"includeText": False}},
                "completionProvider": {"triggerCharacters": ["[", "-"], "resolveProvider": False},
                "hoverProvider": True,
                "definitionProvider": True,
                "documentLinkProvider": {"resolveProvider": False},
            },
            "serverInfo": {"name": "whykit", "version": __version__},
        }

    def _initialized_notification(self, params: JsonObject) -> None:
        if self._watch_registration:
            self.send({"id": "whykit-watch", "method": "client/registerCapability", "params": {"registrations": [{
                "id": "whykit-watch",
                "method": "workspace/didChangeWatchedFiles",
                "registerOptions": {"watchers": [{"globPattern": "**/*.md"}, {"globPattern": "**/whykit.toml"}]},
            }]}})
        for vault in list(self._workspace):
            self._schedule(vault, full=True)

    def _shutdown_request(self, params: JsonObject) -> None:
        self._shutdown = True
        with self._lock:
            for vault in self._vaults.values():
                if vault.timer is not None:
                    vault.timer.cancel()

    # ------------------------------------------------------------------ vaults

    def _path(self, uri: str) -> Path | None:
        try:
            return Path(uri_to_path(uri))
        except ValueError:
            return None

    def _vault_at(self, root: Path) -> _Vault:
        root = root.resolve()
        vault = self._vaults.get(root)
        if vault is None:
            vault = self._vaults[root] = _Vault(root)
        return vault

    def _vault_containing(self, path: Path) -> _Vault | None:
        if self.root is not None:
            vault = self._vault_at(self.root)
            return vault if _within(vault.root, path) else None
        start = path if path.is_dir() else path.parent
        key = os.fspath(start)
        if key not in self._vault_dirs:
            found = find_vault_root(start) if start.exists() else None
            self._vault_dirs[key] = self._vault_at(found) if found is not None else None
        return self._vault_dirs[key]

    def _document(self, params: JsonObject) -> _Document | None:
        """The open document named in *params*, or a read-only view of the file on disk."""
        ident = params.get("textDocument")
        if not isinstance(ident, dict) or not isinstance(ident.get("uri"), str):
            raise _RequestError(INVALID_PARAMS, "textDocument.uri is required")
        uri = ident["uri"]
        with self._lock:
            doc = self._documents.get(uri)
            if doc is not None:
                return doc
            path = self._path(uri)
            if path is None or not path.is_file():
                return None
            vault = self._vault_containing(path)
        if vault is None:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return _Document(uri, path, text, None, vault)

    def _position(self, params: JsonObject) -> tuple[int, int]:
        position = params.get("position")
        if not isinstance(position, dict):
            raise _RequestError(INVALID_PARAMS, "position is required")
        line, character = position.get("line"), position.get("character")
        if not isinstance(line, int) or not isinstance(character, int) or line < 0 or character < 0:
            raise _RequestError(INVALID_PARAMS, "position.line and position.character must be non-negative integers")
        return line, character

    # ------------------------------------------------------- text sync events

    def _did_open(self, params: JsonObject) -> None:
        item = params.get("textDocument")
        if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
            return
        uri = item["uri"]
        path = self._path(uri)
        with self._lock:
            vault = self._vault_containing(path) if path is not None else None
            self._documents[uri] = _Document(uri, path, str(item.get("text", "")), item.get("version"), vault)
        if vault is not None:
            self._schedule(vault, changed=uri)

    def _did_change(self, params: JsonObject) -> None:
        ident = params.get("textDocument") or {}
        with self._lock:
            doc = self._documents.get(str(ident.get("uri"))) if isinstance(ident, dict) else None
            if doc is None:
                return
            text = doc.text
            for change in params.get("contentChanges") or []:
                if isinstance(change, dict):
                    text = apply_change(text, change, self.encoding)
            doc.text = text
            doc.version = ident.get("version", doc.version)
            vault = doc.vault
        if vault is not None:
            self._schedule(vault, changed=doc.uri)

    def _did_save(self, params: JsonObject) -> None:
        ident = params.get("textDocument") or {}
        with self._lock:
            doc = self._documents.get(str(ident.get("uri"))) if isinstance(ident, dict) else None
            vault = doc.vault if doc is not None else None
        if vault is not None:
            self._schedule(vault, full=True)

    def _did_close(self, params: JsonObject) -> None:
        ident = params.get("textDocument") or {}
        with self._lock:
            doc = self._documents.pop(str(ident.get("uri")), None) if isinstance(ident, dict) else None
        if doc is None or doc.vault is None or doc.path is None:
            return
        try:
            unchanged = doc.path.read_text(encoding="utf-8").replace("\r\n", "\n") == doc.text.replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            unchanged = False
        # A closed buffer that matched the file changes nothing; one that did
        # not was being linted in place of the file, so recheck the vault.
        if not unchanged:
            self._schedule(doc.vault, full=True)

    def _did_change_watched(self, params: JsonObject) -> None:
        touched: list[_Vault] = []
        with self._lock:
            for change in params.get("changes") or []:
                if not isinstance(change, dict) or not isinstance(change.get("uri"), str):
                    continue
                path = self._path(change["uri"])
                if path is None:
                    continue
                for vault in self._vaults.values():
                    if _within(vault.root, path) and vault not in touched:
                        touched.append(vault)
        for vault in touched:
            self._schedule(vault, full=True)

    # ---------------------------------------------------------------- linting

    def _schedule(self, vault: _Vault, *, full: bool = False, changed: str | None = None) -> None:
        """Queue a lint of *vault*: the whole vault, or only the edited buffers.

        Typing re-lints only the edited files, which takes a fraction of a
        second at any vault size; opening the workspace, saving, and files
        changing on disk re-lint the whole vault.
        """
        if self._shutdown:
            return
        with self._lock:
            if full or vault.full_findings is None:
                vault.full_pending = True
            if changed is not None:
                vault.changed.add(changed)
            if self.debounce > 0:
                if vault.timer is not None:
                    vault.timer.cancel()
                timer = threading.Timer(self.debounce, self._lint_quietly, (vault,))
                timer.daemon = True
                vault.timer = timer
                timer.start()
                return
        self._lint(vault)

    def _lint_quietly(self, vault: _Vault) -> None:
        try:
            self._lint(vault)
        except Exception as exc:  # noqa: BLE001 - a background failure is reported, not raised
            if os.environ.get("WHYKIT_DEBUG"):
                traceback.print_exc(file=sys.stderr)
            self.log(f"whykit lsp: lint failed: {type(exc).__name__}: {exc}", 1)

    def _overlays(self, vault: _Vault) -> list[tuple[Path, str]]:
        return [(path, text) for path, text, _, _ in self._snapshot(vault)]

    def _snapshot(self, vault: _Vault) -> list[tuple[Path, str, str, Any]]:
        """Path, text, URI and version of every open Markdown buffer in *vault*, taken atomically."""
        with self._lock:
            return [
                (doc.path, doc.text, doc.uri, doc.version) for doc in self._documents.values()
                if doc.vault is vault and doc.path is not None and doc.path.suffix.lower() == ".md"
            ]

    def _build_index(self, vault: _Vault, overlays: list[tuple[Path, str]], *, rescan: bool) -> VaultIndex:
        """Disk notes (reusing unchanged parses) with open buffers laid over them.

        Call inside ``path_cache()``.  Without *rescan* the last disk scan is
        reused, so a keystroke does not stat every file of a large vault.
        """
        root = vault.root
        if rescan or vault.disk is None:
            disk = vault.notes.load_all(collect_markdown(root, []))
            vault.disk = disk
            vault.disk_by_path = {_real(note.path): note for note in disk}
            vault.disk_keys = {_key(note.path): number for number, note in enumerate(disk)}
        notes = list(vault.disk)
        by_path = dict(vault.disk_by_path)
        depth = len(root.parts)
        for path, text in overlays:
            number = vault.disk_keys.get(_key(path))
            if number is not None:
                note = load_note(notes[number].path, text=text)
                notes[number] = note
            else:
                real = _real(path)
                if not _within(root, real) or any(part in CONTENT_SKIP_DIRS for part in real.parts[depth:]):
                    continue
                note = load_note(real, text=text)
                notes.append(note)
            by_path[_real(note.path)] = note
        return VaultIndex(root=root, notes=notes, by_path=by_path, link_index=_build_index(notes))

    def _lint(self, vault: _Vault) -> None:
        with vault.lint_lock:
            with self._lock:
                full = vault.full_pending or vault.full_findings is None
                changed = [self._documents[uri] for uri in sorted(vault.changed) if uri in self._documents]
                vault.full_pending = False
                vault.changed = set()
            snapshot = self._snapshot(vault)
            overlays = [(path, text) for path, text, _, _ in snapshot]
            versions = {uri: version for _, _, uri, version in snapshot}
            if full:
                vault.paths = _RequestCache()
            with _paths(vault):
                index = self._build_index(vault, overlays, rescan=full)
                paths: list[str] = []
                if not full:
                    for doc in changed:
                        if doc.path is None or vault.disk_keys.get(_key(doc.path)) is None:
                            full = True  # a new, unsaved file: only a whole-vault lint sees it
                            break
                        paths.append(index.relative(doc.path))
                if not full and not paths:
                    return
                _, findings = lint(vault.root, paths or None, vault=index, today=self.today)
                with self._lock:
                    vault.index = index
                    vault.decisions = None
                    self.lint_runs += 1
                if full:
                    self._publish_full(vault, index, findings, versions)
                else:
                    self._publish_scoped(vault, index, changed, findings, versions)

    def _by_file(self, vault: _Vault, findings: list[Finding]) -> dict[str, tuple[Path, list[Finding]]]:
        grouped: dict[str, tuple[Path, list[Finding]]] = {}
        for finding in findings:
            path = Path(finding.path)
            if not path.is_absolute():
                path = vault.root / PurePosixPath(finding.path)
            grouped.setdefault(_key(path), (path, []))[1].append(finding)
        return grouped

    def _send_diagnostics(self, uri: str, diagnostics: list[JsonObject], versions: dict[str, Any]) -> None:
        params: JsonObject = {"uri": uri, "diagnostics": diagnostics}
        if isinstance(versions.get(uri), int):
            params["version"] = versions[uri]
        self.notify("textDocument/publishDiagnostics", params)

    def _publish_full(self, vault: _Vault, index: VaultIndex, findings: list[Finding], versions: dict[str, Any]) -> None:
        with self._lock:
            open_docs = {_key(doc.path): doc for doc in self._documents.values()
                         if doc.vault is vault and doc.path is not None}
        grouped = self._by_file(vault, findings)
        vault.full_findings = {key: items for key, (_, items) in grouped.items()}
        outgoing: dict[str, list[JsonObject]] = {}
        for key, (path, items) in grouped.items():
            doc = open_docs.get(key)
            uri = doc.uri if doc is not None else path_to_uri(path)
            # The text that was linted: an open buffer as it was, or the file.
            lines = self._disk_lines(index, path)
            outgoing[uri] = [self._diagnostic(item, lines) for item in items]
        for doc in open_docs.values():
            outgoing.setdefault(doc.uri, [])
        with self._lock:
            stale = vault.published - set(outgoing)
            vault.published = {uri for uri, diagnostics in outgoing.items() if diagnostics}
        for uri in sorted(stale):
            self._send_diagnostics(uri, [], versions)
        for uri, diagnostics in outgoing.items():
            self._send_diagnostics(uri, diagnostics, versions)

    def _publish_scoped(self, vault: _Vault, index: VaultIndex, docs: list[_Document], findings: list[Finding],
                        versions: dict[str, Any]) -> None:
        """Diagnostics for edited buffers: their own findings, fresh, plus the
        cross-file findings of the last whole-vault lint (refreshed on save)."""
        grouped = self._by_file(vault, findings)
        previous = vault.full_findings or {}
        for doc in docs:
            assert doc.path is not None
            key = _key(doc.path)
            items = list(grouped.get(key, (doc.path, []))[1])
            seen = {(item.line, item.code, item.message) for item in items}
            for item in previous.get(key, []):
                if item.code in CROSS_FILE_CODES and (item.line, item.code, item.message) not in seen:
                    items.append(item)
            lines = self._disk_lines(index, doc.path)
            diagnostics = [self._diagnostic(item, lines) for item in items]
            with self._lock:
                (vault.published.add if diagnostics else vault.published.discard)(doc.uri)
            self._send_diagnostics(doc.uri, diagnostics, versions)

    def _disk_lines(self, index: VaultIndex, path: Path) -> list[str]:
        note = index.note_for(path)
        if note is not None:
            return _split_lines(note.text)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                return _split_lines(handle.read(4 * 1024 * 1024))
        except OSError:
            return []

    def _diagnostic(self, finding: Finding, lines: list[str]) -> JsonObject:
        line_no = max(0, (finding.line or 1) - 1)
        text = lines[line_no] if line_no < len(lines) else ""
        start, end = _finding_span(text, finding)
        return {
            "range": self._range(line_no, text, start, end),
            "severity": SEVERITY.get(finding.level, 3),
            "code": finding.code,
            "source": "whykit",
            "message": finding.message,
        }

    def _range(self, line_no: int, text: str, start: int, end: int) -> JsonObject:
        return {
            "start": {"line": line_no, "character": to_character(text, start, self.encoding)},
            "end": {"line": line_no, "character": to_character(text, end, self.encoding)},
        }

    # ------------------------------------------------------------- navigation

    def _index(self, vault: _Vault) -> VaultIndex:
        """The index of the last lint, or a fresh one when there was none yet."""
        with self._lock:
            index = vault.index
        if index is None:
            with _paths(vault):
                index = self._build_index(vault, self._overlays(vault), rescan=False)
            with self._lock:
                vault.index = index
                vault.decisions = None
        return index

    def _decisions(self, vault: _Vault, index: VaultIndex) -> dict[str, Note]:
        with self._lock:
            if vault.decisions is not None and vault.index is index:
                return vault.decisions
        found: dict[str, Note] = {}
        for note in index.notes:
            if str(note.front.get("status", "")).strip() == "template":
                continue
            explicit = str(note.front.get("decision_id") or "").strip()
            did = explicit if re.fullmatch(r"D-[0-9]{3,}", explicit) else None
            if did is None and "06-decisions" in note.path.parts and DECISION_FILE_RE.match(note.path.name):
                did = _decision_own_id(note)
            if did is not None:
                found.setdefault(did, note)
        with self._lock:
            if vault.index is index:
                vault.decisions = found
        return found

    def _evidence(self, vault: _Vault) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
        register = vault.root / REGISTER
        with self._lock:
            open_text = next((doc.text for doc in self._documents.values()
                              if doc.path is not None and doc.vault is vault and _key(doc.path) == _key(register)), None)
        if open_text is not None:
            active, retired, _ = _parse_evidence_register_text(open_text.replace("\r\n", "\n"))
            return active, retired
        active, retired, _ = evidence_register(vault.root)
        return active, retired

    def _uri_for(self, path: Path) -> str:
        key = _key(path)
        with self._lock:
            for doc in self._documents.values():
                if doc.path is not None and _key(doc.path) == key:
                    return doc.uri
        return path_to_uri(path)

    def _location(self, path: Path, line: int = 0) -> JsonObject:
        position = {"line": line, "character": 0}
        return {"uri": self._uri_for(path), "range": {"start": position, "end": position}}

    def _at(self, params: JsonObject) -> tuple[_Document, VaultIndex, int, str, str, int] | None:
        """Document, index, line number, raw and masked line, string index under the cursor."""
        doc = self._document(params)
        line_no, character = self._position(params)
        if doc is None or doc.vault is None:
            return None
        raw, masked = doc.lines()
        if line_no >= len(raw):
            return None
        index = self._index(doc.vault)
        return doc, index, line_no, raw[line_no], masked[line_no], from_character(raw[line_no], character, self.encoding)

    def _token(self, masked: str, at: int) -> tuple[str, re.Match[str]] | None:
        for match in WIKILINK_RE.finditer(masked):
            if match.start() <= at <= match.end():
                return "link", match
        for match in ID_RE.finditer(masked):
            if match.start() <= at <= match.end():
                return "id", match
        return None

    def _resolve_link(self, index: VaultIndex, target: str) -> Path | None:
        path, ambiguous = index.resolve_link(target.strip())
        return None if ambiguous else path

    def _hover(self, params: JsonObject) -> JsonObject | None:
        found = self._at(params)
        if found is None:
            return None
        doc, index, line_no, raw, masked, at = found
        assert doc.vault is not None
        token = self._token(masked, at)
        if token is None:
            return None
        kind, match = token
        if kind == "link":
            path = self._resolve_link(index, match.group(1))
            note = index.note_for(path) if path is not None else None
            if note is None:
                value = f"`[[{match.group(1).strip()}]]` does not resolve to a note in this vault"
            else:
                value = _note_card(index, note)
        else:
            value = self._id_card(doc.vault, index, match.group(0))
        return {"contents": {"kind": "markdown", "value": value},
                "range": self._range(line_no, raw, match.start(), match.end())}

    def _id_card(self, vault: _Vault, index: VaultIndex, ident: str) -> str:
        if ident.startswith("E-"):
            active, retired = self._evidence(vault)
            if ident in active:
                row = active[ident]
                lines = [f"**{ident}** · active evidence", "", f"**Source:** {row['source']}"]
                for label, key in (("Type", "type"), ("Date", "date"), ("Accessed", "accessed"),
                                   ("Location", "location"), ("Supports", "claims")):
                    if row.get(key):
                        lines.append(f"**{label}:** {row[key]}")
                return "  \n".join(lines)
            if ident in retired:
                row = retired[ident]
                lines = [f"**{ident}** · retired evidence", "", f"**Source:** {row['source']}"]
                for label, key in (("Retired", "retired_on"), ("Why", "why"), ("Replaced by", "replaced_by")):
                    if row.get(key):
                        lines.append(f"**{label}:** {row[key]}")
                return "  \n".join(lines)
            return f"**{ident}** is not in the evidence register"
        note = self._decisions(vault, index).get(ident)
        if note is None:
            return f"No decision record has the ID **{ident}**"
        return _note_card(index, note)

    def _definition(self, params: JsonObject) -> JsonObject | None:
        found = self._at(params)
        if found is None:
            return None
        doc, index, _, raw, masked, at = found
        assert doc.vault is not None
        token = self._token(masked, at)
        if token is None:
            return None
        kind, match = token
        if kind == "link":
            target = match.group(1)
            path = self._resolve_link(index, target)
            if path is None:
                attachment = self._attachment(doc, target)
                return self._location(attachment) if attachment is not None else None
            note = index.note_for(path)
            heading = _link_heading(raw[match.start():match.end()])
            line = _heading_line(note.text, heading) if note is not None and heading else 0
            return self._location(path, line)
        ident = match.group(0)
        if ident.startswith("E-"):
            active, retired = self._evidence(doc.vault)
            row = active.get(ident) or retired.get(ident)
            if row is None:
                return None
            return self._location(doc.vault.root / REGISTER, max(0, int(row.get("line", "1")) - 1))
        note = self._decisions(doc.vault, index).get(ident)
        return self._location(note.path) if note is not None else None

    def _attachment(self, doc: _Document, target: str) -> Path | None:
        assert doc.vault is not None
        normalized = target.strip().replace("\\", "/")
        if not normalized or normalized.lower().endswith(".md"):
            return None
        candidates = [doc.vault.root / normalized.lstrip("/")]
        if doc.path is not None:
            candidates.append(doc.path.parent / normalized)
        for candidate in candidates:
            if candidate.is_file() and _within(doc.vault.root, candidate):
                return candidate
        return None

    def _document_links(self, params: JsonObject) -> list[JsonObject]:
        doc = self._document(params)
        if doc is None or doc.vault is None:
            return []
        index = self._index(doc.vault)
        raw, masked = doc.lines()
        links: list[JsonObject] = []
        for line_no, (line, hidden) in enumerate(zip(raw, masked, strict=True)):
            if "[" not in hidden:
                continue
            for match in WIKILINK_RE.finditer(hidden):
                target = match.group(1)
                if not target.strip() or URL_SCHEME_RE.match(target.strip()):
                    continue
                path = self._resolve_link(index, target)
                if path is None:
                    path = self._attachment(doc, target)
                if path is not None:
                    links.append({"range": self._range(line_no, line, match.start(1), match.end(1)),
                                  "target": self._uri_for(path)})
            for match in MARKDOWN_LINK_RE.finditer(hidden):
                target = match.group(2).strip()
                if target.startswith("<") and ">" in target:
                    target = target[1:target.index(">")]
                else:
                    target = target.split(None, 1)[0] if target else ""
                if not target or target.startswith("#") or URL_SCHEME_RE.match(target):
                    continue
                local = unquote(target.split("#", 1)[0].split("?", 1)[0])
                if not local:
                    continue
                base = doc.vault.root if local.startswith("/") else (doc.path.parent if doc.path else doc.vault.root)
                candidate = base / local.lstrip("/")
                if candidate.exists() and _within(doc.vault.root, candidate):
                    start = match.start(2) + match.group(2).index(target) if target in match.group(2) else match.start(2)
                    links.append({"range": self._range(line_no, line, start, start + len(target)),
                                  "target": self._uri_for(candidate)})
        return links

    def _completion(self, params: JsonObject) -> JsonObject:
        empty = {"isIncomplete": False, "items": []}
        found = self._at(params)
        if found is None:
            return empty
        doc, index, line_no, raw, _, at = found
        assert doc.vault is not None
        before = raw[:at]
        opening = before.rfind("[[")
        if opening >= 0 and "]]" not in before[opening:]:
            typed = before[opening + 2:]
            if "|" in typed or "#" in typed:
                return empty
            return self._link_items(doc.vault, index, line_no, raw, opening + 2, at, typed)
        match = ID_PREFIX_RE.search(before)
        if match is None:
            return empty
        return self._id_items(doc.vault, index, line_no, raw, match.start(1), at, match.group(1))

    def _link_items(self, vault: _Vault, index: VaultIndex, line_no: int, raw: str,
                    start: int, end: int, typed: str) -> JsonObject:
        needle = typed.strip().casefold()
        closing = "" if raw[end:].startswith(("]]", "|", "#")) else "]]"
        edit_range = self._range(line_no, raw, start, end)
        candidates: list[JsonObject] = []

        def unique(name: str, note: Note) -> bool:
            hits = index.link_index.get(unicodedata.normalize("NFC", name).casefold(), set())
            return hits == {note.path}

        def shortest(note: Note) -> str:
            relative = index.relative(note.path).removesuffix(".md")
            return note.path.stem if unique(note.path.stem, note) else relative

        def add(label: str, insert: str, kind: int, detail: str, filter_text: str, sort: str) -> None:
            if needle and needle not in filter_text.casefold():
                return
            candidates.append({
                "label": label, "kind": kind, "detail": detail, "filterText": filter_text,
                "sortText": sort, "textEdit": {"range": edit_range, "newText": insert + closing},
            })

        decisions = self._decisions(vault, index)
        for did, note in sorted(decisions.items()):
            title = str(note.front.get("title") or note.path.stem)
            insert = did if unique(did, note) else shortest(note)
            add(did, insert, KIND_REFERENCE, f"{title} · {note.front.get('status') or 'no status'}", f"{did} {title}", f"0{did}")
        for note in index.notes:
            if str(note.front.get("status", "")).strip() == "template":
                continue
            relative = index.relative(note.path).removesuffix(".md")
            title = str(note.front.get("title") or note.path.stem)
            add(relative, shortest(note), KIND_FILE, title, relative, f"1{relative}")
            aliases = note.front.get("aliases")
            for alias in aliases if isinstance(aliases, list) else []:
                alias = str(alias).strip()
                if alias and alias not in decisions and unique(alias, note):
                    add(alias, alias, KIND_REFERENCE, f"alias of {relative}", alias, f"2{alias}")
        candidates.sort(key=lambda item: item["sortText"])
        return {"isIncomplete": len(candidates) > COMPLETION_LIMIT, "items": candidates[:COMPLETION_LIMIT]}

    def _id_items(self, vault: _Vault, index: VaultIndex, line_no: int, raw: str,
                  start: int, end: int, typed: str) -> JsonObject:
        edit_range = self._range(line_no, raw, start, end)
        items: list[JsonObject] = []
        if typed.startswith("E"):
            active, retired = self._evidence(vault)
            for ident, row in sorted({**retired, **active}.items()):
                if not ident.startswith(typed):
                    continue
                item: JsonObject = {
                    "label": ident, "kind": KIND_CONSTANT, "detail": row.get("source", ""),
                    "documentation": row.get("claims") or row.get("why") or "",
                    "textEdit": {"range": edit_range, "newText": ident},
                }
                if ident not in active:
                    item["tags"] = [TAG_DEPRECATED]
                    item["detail"] = f"{row.get('source', '')} (retired)"
                items.append(item)
        else:
            for ident, note in sorted(self._decisions(vault, index).items()):
                if ident.startswith(typed):
                    title = str(note.front.get("title") or note.path.stem)
                    items.append({
                        "label": ident, "kind": KIND_REFERENCE,
                        "detail": f"{title} · {note.front.get('status') or 'no status'}",
                        "textEdit": {"range": edit_range, "newText": ident},
                    })
        return {"isIncomplete": len(items) > COMPLETION_LIMIT, "items": items[:COMPLETION_LIMIT]}


_HANDLERS: dict[str, Callable[[LanguageServer, JsonObject], Any]] = {
    "initialized": LanguageServer._initialized_notification,
    "shutdown": LanguageServer._shutdown_request,
    "textDocument/didOpen": LanguageServer._did_open,
    "textDocument/didChange": LanguageServer._did_change,
    "textDocument/didSave": LanguageServer._did_save,
    "textDocument/didClose": LanguageServer._did_close,
    "workspace/didChangeWatchedFiles": LanguageServer._did_change_watched,
    "textDocument/hover": LanguageServer._hover,
    "textDocument/definition": LanguageServer._definition,
    "textDocument/completion": LanguageServer._completion,
    "textDocument/documentLink": LanguageServer._document_links,
}


# --------------------------------------------------------------------------
# Helpers.


def _finding_span(text: str, finding: Finding) -> tuple[int, int]:
    """Columns of the part of *text* a finding is about; the whole line otherwise."""
    link = MESSAGE_LINK_RE.search(finding.message)
    if link is not None:
        at = text.find("[[" + link.group(1))
        if at >= 0:
            close = text.find("]]", at)
            return at, (close + 2 if close >= 0 else at + len(link.group(1)) + 2)
    paren = MESSAGE_PAREN_RE.search(finding.message)
    if paren is not None and finding.code.startswith("markdown_link"):
        at = text.find(paren.group(1))
        if at >= 0:
            return at, at + len(paren.group(1))
    for ident in ID_RE.findall(finding.message):
        match = re.search(rf"\b{re.escape(ident)}\b", text)
        if match is not None:
            return match.start(), match.end()
    stripped = text.rstrip()
    start = len(stripped) - len(stripped.lstrip())
    return start, len(stripped)


def _note_card(index: VaultIndex, note: Note) -> str:
    front = note.front
    title = str(front.get("title") or note.path.stem)
    lines = [f"**{title}**", "", f"`{index.relative(note.path)}`", ""]
    for label, key in (("Type", "type"), ("Status", "status"), ("Owner", "owner"),
                       ("Review by", "review_by"), ("Decision", "decision_id"),
                       ("Supersedes", "supersedes"), ("Superseded by", "superseded_by")):
        value = front.get(key)
        if value not in (None, "", []):
            lines.append(f"**{label}:** {', '.join(map(str, value)) if isinstance(value, list) else value}")
    sources = front.get("source_ids")
    if isinstance(sources, list) and sources:
        lines.append(f"**Evidence:** {', '.join(map(str, sources))}")
    return "  \n".join(lines)


def _link_heading(link_text: str) -> str | None:
    inner = link_text.strip("![]")
    inner = inner.split("|", 1)[0]
    if "#" not in inner:
        return None
    heading = inner.split("#", 1)[1].strip()
    return heading or None


def _heading_line(text: str, heading: str) -> int:
    wanted = heading.casefold()
    for number, line in enumerate(text.split("\n")):
        match = HEADING_RE.match(line)
        if match is not None and match.group(1).casefold() == wanted:
            return number
    return 0


# --------------------------------------------------------------------------
# Entry points.


def serve(
    stdin: IO[bytes],
    stdout: IO[bytes],
    *,
    root: Path | None = None,
    debounce: float = 0.3,
    today: dt.date | None = None,
) -> int:
    """Run the server until ``exit`` or end of input; return the exit code."""
    write_lock = threading.Lock()

    def send(message: JsonObject) -> None:
        with write_lock:
            write_message(stdout, message)

    server = LanguageServer(send, root=root, debounce=debounce, today=today)
    while server.running:
        try:
            message = read_message(stdin)
        except (ProtocolError, ValueError) as exc:
            # json.JSONDecodeError and UnicodeDecodeError are ValueErrors too.
            send({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": str(exc)}})
            continue
        if message is None:
            server._stop()
            return 0 if server.exit_code == 0 else 1
        server.handle(message)
    return server.exit_code if server.exit_code is not None else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whykit lsp",
        description="Run the read-only WhyKit language server over stdio.",
    )
    parser.add_argument("--root", help="serve only this vault (default: the vault around each open file)")
    parser.add_argument("--debounce", type=int, default=300, help="milliseconds to wait after an edit before linting (default: %(default)s)")
    parser.add_argument("--today", help="evaluate review dates as of this YYYY-MM-DD date instead of today")
    parser.add_argument("--stdio", action="store_true", help="accepted for editor clients that pass it; stdio is the only transport")
    return parser


def main(argv: list[str] | None = None) -> int:
    from .contract import emit_error, vault_not_found

    args = build_parser().parse_args(argv)
    today = None
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            return emit_error("invalid_argument", f"--today is not a real ISO date: {args.today}", json_mode=False)
    if args.debounce < 0:
        return emit_error("invalid_argument", "--debounce must be >= 0", json_mode=False)
    root = None
    if args.root:
        root = Path(args.root).expanduser().resolve()
        if not is_vault_root(root):
            return vault_not_found(args.root, json_mode=False)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    # The protocol owns stdout: anything else printed there would corrupt a frame.
    with contextlib.redirect_stdout(sys.stderr):
        return serve(stdin, stdout, root=root, debounce=args.debounce / 1000, today=today)


__all__ = [
    "LanguageServer", "ProtocolError", "apply_change", "from_character", "main", "path_to_uri",
    "read_message", "serve", "to_character", "uri_to_path", "write_message",
]
