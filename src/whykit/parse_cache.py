"""Persistent parse cache: skip re-reading unchanged notes on the next run.

Every read command parses the whole vault.  On a large vault most of that work
repeats from one run to the next, because most files did not change.  This
module keeps, per note, what WhyKit derives from its text alone (front matter,
wikilink and Markdown link targets, fact callouts, credential-shaped matches)
in ``.whykit/cache/`` and reuses it while the file is unchanged.  Front matter
lives in ``notes.v1``; each derived view has a file of its own, read only by a
command that needs it.  Everything
that depends on more than one file -- link resolution, findings, the graph --
is still computed on every run.

Correctness rules, in order of precedence:

* An entry is reused only when the file's ``stat`` signature (modification
  and change time in nanoseconds, size, inode) is exactly the one recorded
  when the entry was parsed.  Any difference means a re-parse.  The change
  time cannot be set by a user, so neither ``touch -d``/``os.utime`` nor a
  copied-in cache file from another checkout can fake a match.
* An entry whose file changed within two seconds of being parsed ("racy",
  as Git calls it), or whose timestamps lie in the future, is reused only
  after the file's SHA-256 matches the recorded one.  This covers file
  systems with coarse timestamps and clocks that moved.
* The whole cache is discarded when the WhyKit version, its source code, the
  Python version, the Unicode database or ``whykit.toml`` changes.
* A cache that cannot be read or does not have the expected shape is ignored
  with a warning and rebuilt.  An unreadable or unwritable cache directory
  just means no cache.
* Credential-shaped matches are stored per note under the same content rule,
  so any change to a note (its ``sensitivity`` included) is scanned again;
  files that are not notes are scanned on every run.
* Note bodies are never stored.  A note's text is read again only if a
  command needs it (full-text search, context packs), and then its SHA-256
  must still match; if it does not, the note is parsed afresh.

Writes are atomic (temporary file plus ``os.replace``), take a lock of their
own and are skipped while another process holds it or while a WhyKit command
is modifying the vault, so concurrent runs never see a partial file.

``--no-cache`` (``whykit --no-cache lint``) or ``WHYKIT_NO_CACHE=1`` turns
the cache off.  Outputs are byte-identical either way.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import gc
import hashlib
import json
import os
import secrets
import stat as stat_mod
import sys
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import __version__
from .config import CONFIG_FILE
from .console import one_line
from .lint import Note, _normalise, _shared, _shared_target, load_note

CACHE_DIR = Path(".whykit") / "cache"
# No extension the secret scan or the Markdown collector looks at.
CACHE_FILE = "notes.v1"
# File names keep the layout version; FORMAT (stored inside, and part of the
# fingerprint) is what invalidates entries.
VIEW_SUFFIX = ".v1"
LOCK_FILE = "write.lock"
# Bumped whenever what a parse produces changes shape or meaning (2: link and
# table parsing became linear, decision and evidence IDs ASCII-digit only, and
# files that are not valid UTF-8 parse with a finding instead of failing).
# The fingerprint also hashes the package source, so this is the explicit
# signal for installed copies whose version string did not move.
FORMAT = 2
ENV_DISABLE = "WHYKIT_NO_CACHE"
RACY_NS = 2_000_000_000

# Derived views of a note's text that the cache stores when a run computed
# them.  Each is a ``functools.cached_property`` on ``Note``; a view a run did
# not need is simply absent and computed from the text when a later run does.
NOTE_FACTS: tuple[str, ...] = (
    "cited_evidence",
    "wikilink_hits",
    "markdown_link_hits",
    "fact_callouts",
    "section_decision_id",
    "secret_hits",
    "decision_placeholders",
)


class _Corrupt(ValueError):
    """A stored value does not have the shape its view returns."""


def _need(condition: bool) -> None:
    if not condition:
        raise _Corrupt("unexpected value in parse cache")


def _int(value: Any) -> int:
    _need(type(value) is int)
    return value


def _str(value: Any) -> str:
    _need(type(value) is str)
    return _shared_target(value)


def _bool(value: Any) -> bool:
    _need(type(value) is bool)
    return value


def _kind(value: Any) -> str:
    _need(value in ("link", "image"))
    return "image" if value == "image" else "link"


def _list(value: Any) -> list:
    _need(type(value) is list)
    return value


def _rows(value: Any, width: int) -> list:
    _need(type(value) is list and all(type(row) is list and len(row) == width for row in value))
    return value


# How each stored view turns back into what its ``Note`` property returns.
# Every conversion checks the shape, so a damaged entry is recomputed from the
# note's text instead of producing a wrong finding.
_THAW: dict[str, Any] = {
    "cited_evidence": lambda value: tuple(_str(item) for item in _list(value)),
    "wikilink_hits": lambda value: tuple(
        (_str(target), _int(line), _bool(embed)) for target, line, embed in _rows(value, 3)
    ),
    "markdown_link_hits": lambda value: tuple(
        (_kind(kind), _str(target), _int(line)) for kind, target, line in _rows(value, 3)
    ),
    "fact_callouts": lambda value: tuple(
        (_int(line), tuple(_str(item) for item in _list(cited))) for line, cited in _rows(value, 2)
    ),
    "section_decision_id": lambda value: None if value is None else _str(value),
    "secret_hits": lambda value: tuple((_int(line), _str(label)) for line, label in _rows(value, 2)),
    "decision_placeholders": lambda value: tuple((_int(line), _str(section)) for line, section in _rows(value, 2)),
}


def _thaw_front(value: Any) -> Any:
    """Check a stored front-matter value and share its short strings, in place.

    Front matter is made of str, bool, None, lists and one level of mapping;
    JSON round-trips all of them.  The decoded containers become the note's
    own, so nothing is copied.
    """
    kind = type(value)
    if kind is str:
        return _shared(value)
    if kind is list:
        for index, item in enumerate(value):
            value[index] = _shared(item) if type(item) is str else _thaw_front(item)
        return value
    if kind is dict:
        for key, item in value.items():
            value[key] = _shared(item) if type(item) is str else _thaw_front(item)
        return value
    _need(value is None or kind is bool)
    return value


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)


def _settled(info: os.stat_result, now_ns: int) -> bool:
    """Whether a file's timestamps are safely in the past at *now_ns*.

    A write within the same timestamp tick, with the same size, would leave
    the signature unchanged.  Two seconds covers the coarsest common
    timestamp resolution (FAT); a timestamp in the future means the clock
    cannot be trusted, so such an entry is always verified by content.
    """
    newest = max(info.st_mtime_ns, info.st_ctime_ns)
    return now_ns - newest >= RACY_NS


def _decode(data: bytes) -> str:
    # Same text as Path.read_text(encoding="utf-8"): strict UTF-8, universal
    # newlines (load_note translates the line endings of supplied text).
    return data.decode("utf-8")


_MISSING = object()


class CachedNote(Note):
    """A note restored from the cache.  Its text is read only when used.

    The text must hash to the digest recorded with the parsed fields.  If the
    file changed since the run's ``stat`` (a concurrent edit), the note is
    re-parsed from what is on disk now, as an uncached run reading the file at
    this moment would have done, and it is not written back to the cache.

    Each stored view is read from its own file the first time a command asks
    for it, so a command that needs only the front matter never loads them.
    """

    def __init__(self, path: Path, digest: str, fields: list, cache: DiskCache, key: str) -> None:  # noqa: D107
        front, has_front, front_error, body_offset = fields
        self.path = path
        self._text: str | None = None
        self.digest = digest
        self.front = front
        self.has_front = has_front
        self.front_error = front_error
        self.body_offset = body_offset
        self.stale = False
        self.cache = cache
        self.key = key

    @property  # type: ignore[override]
    def text(self) -> str:
        if self._text is None:
            data = self.path.read_bytes()
            if hashlib.sha256(data).hexdigest() == self.digest:
                self._text = _normalise(_decode(data))
            else:
                try:
                    fresh = load_note(self.path, text=_decode(data))
                except UnicodeDecodeError:
                    fresh = load_note(self.path)  # raises like an uncached load
                self.stale = True
                self.cache.stale.add(self.key)
                for name in NOTE_FACTS + ("masked",):
                    self.__dict__.pop(name, None)
                self.front = fresh.front
                self.has_front = fresh.has_front
                self.front_error = fresh.front_error
                self.body_offset = fresh.body_offset
                self._text = fresh.text
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = value


class FreshNote(Note):
    """A note parsed in this run; views it computes are handed to the cache.

    The cache keeps the computed values rather than the note, so a command
    that is done with its notes can free them before the cache is written.
    """

    cache: DiskCache
    key: str
    digest: str


def _cached_view(name: str) -> functools.cached_property:
    compute = getattr(Note, name).func

    def view(self: CachedNote) -> Any:
        if not self.stale:
            raw = self.cache.stored_view(name, self.key, self.digest)
            if raw is not _MISSING:
                try:
                    return _THAW[name](raw)
                except (_Corrupt, TypeError, ValueError):
                    _warn(f"parse cache entry for {self.path.name} is damaged; recomputing it")
        value = compute(self)
        if not self.stale:
            self.cache.computed(name, self.key, value)
        return value

    def fresh_view(self: FreshNote) -> Any:
        value = compute(self)
        self.cache.computed(name, self.key, value)
        return value

    prop = functools.cached_property(view)
    prop.__set_name__(CachedNote, name)
    setattr(CachedNote, name, prop)
    fresh = functools.cached_property(fresh_view)
    fresh.__set_name__(FreshNote, name)
    setattr(FreshNote, name, fresh)
    return prop


for _name in NOTE_FACTS:
    _cached_view(_name)
del _name


class _Entry:
    __slots__ = ("signature", "digest", "settled", "fields")

    def __init__(self, signature: tuple[int, int, int, int], digest: str, settled: bool, fields: list) -> None:
        self.signature = signature
        self.digest = digest
        self.settled = settled
        self.fields = fields


def fingerprint(root: Path) -> str:
    """Everything outside a note that its cached parse depends on."""
    digest = hashlib.sha256()
    digest.update(f"format={FORMAT}\nversion={__version__}\n".encode())
    digest.update(f"python={sys.version_info[0]}.{sys.version_info[1]}\nunicode={unicodedata.unidata_version}\n".encode())
    package = Path(__file__).resolve().parent
    # The parser's own source: a development checkout changes behaviour without
    # changing the version number.  The template supplies placeholder prompts.
    sources = sorted(package.glob("*.py")) + sorted(p for p in (package / "template").rglob("*") if p.is_file())
    for path in sources:
        try:
            data = path.read_bytes()
        except OSError:
            data = b"<unreadable>"
        digest.update(path.relative_to(package).as_posix().encode("utf-8", "surrogateescape"))
        digest.update(hashlib.sha256(data).digest())
    try:
        digest.update(b"config=" + hashlib.sha256((root / CONFIG_FILE).read_bytes()).digest())
    except OSError:
        digest.update(b"config=<none>")
    return digest.hexdigest()


def _warn(message: str) -> None:
    try:
        print(f"warning: {one_line(message)}", file=sys.stderr)
    except (OSError, ValueError):
        pass


def _no_symlinks(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat_mod.S_ISLNK(os.lstat(current).st_mode):
                return False
        except FileNotFoundError:
            return True
        except OSError:
            return False
    return True


@contextlib.contextmanager
def _no_gc() -> Iterator[None]:
    """Pause the cycle collector while building acyclic containers in bulk.

    Decoding the cache creates hundreds of thousands of lists and dicts; each
    allocation burst would otherwise trigger collections that walk all of them
    again.  Nothing created here holds a reference cycle.
    """
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


def _open_nofollow(path: Path, flags: int) -> int:
    """``os.open`` that refuses to follow a symlink at *path* where supported."""
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o644)


Record = tuple[tuple[int, int, int, int], str, bool]


def _view_tag(digest: str) -> str:
    """The part of a note's digest a view row carries to say which content it is for.

    64 bits: the row is only ever compared with the digest of the same path's
    current content, which ``notes.v1`` checked in full.
    """
    return digest[:16]


class DiskCache:
    """The parse cache of one vault for the duration of one command.

    ``notes.v1`` holds each note's ``stat`` signature, digest and front
    matter.  Each derived view has a file of its own (``wikilink_hits.v1`` and
    so on), keyed by the same digest and read only when a command first asks
    for that view.  Any of them can be missing or stale without affecting the
    others.

    ``notes.v1`` is written as soon as the notes are loaded, when it changed;
    the view files when the command ends, from the views it computed.  The
    cache never keeps the notes themselves alive.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / CACHE_DIR
        self.file = self.directory / CACHE_FILE
        self.fingerprint = fingerprint(root)
        self.entries: dict[str, _Entry] = {}
        # Digest of each note the last load recorded, for the view files.
        self.digests: dict[str, str] | None = None
        self.stale: set[str] = set()
        self.dirty = False
        self.hits = 0
        self.misses = 0
        self._views: dict[str, dict[str, Any]] = {}
        # Views whose file must be rewritten, with the values this run computed.
        self._computed: dict[str, dict[str, Any]] = {}
        self._broken_views: set[str] = set()
        self._read()

    # -- reading ---------------------------------------------------------

    def _load_json(self, path: Path) -> Any:
        """The decoded document at *path*, or ``None`` when there is none to trust."""
        if not _no_symlinks(self.root, path.relative_to(self.root)):
            return None
        try:
            info = os.stat(path)
        except OSError:
            return None
        # Only trust a cache this user wrote.
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return None
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            return None
        with _no_gc():
            data = json.loads(raw)
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("unknown format")
        if data.get("fingerprint") != self.fingerprint:
            return None  # WhyKit, Python or whykit.toml changed
        return data

    def _read(self) -> None:
        try:
            data = self._load_json(self.file)
            if data is None:
                self.dirty = os.path.lexists(self.file)
                return
            entries: dict[str, _Entry] = {}
            items = data["entries"]
            if not isinstance(items, dict):
                raise ValueError("entries is not an object")
            for key, row in items.items():
                mtime, ctime, size, ino, digest, settled, fields = row
                if not (
                    all(type(value) is int for value in (mtime, ctime, size, ino))
                    and isinstance(digest, str) and len(digest) == 64
                    and type(settled) is bool
                    and isinstance(fields, list) and len(fields) == 4
                    and isinstance(fields[0], dict) and type(fields[1]) is bool
                    and (fields[2] is None or isinstance(fields[2], str)) and type(fields[3]) is int
                ):
                    raise ValueError(f"malformed entry for {key!r}")
                entries[key] = _Entry((mtime, ctime, size, ino), digest, settled, fields)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            _warn(f"ignoring unreadable parse cache {self.file} ({exc}); it will be rebuilt")
            self.dirty = True
            return
        self.entries = entries

    def _view_file(self, name: str) -> Path:
        return self.directory / f"{name}{VIEW_SUFFIX}"

    def _decode_view(self, name: str) -> dict[str, Any]:
        path = self._view_file(name)
        try:
            data = self._load_json(path)
            if data is None:
                if os.path.lexists(path):
                    self._broken_views.add(name)  # from another WhyKit or config
                return {}
            rows = data["rows"]
            if not isinstance(rows, dict):
                raise ValueError("rows is not an object")
            return rows
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            if name not in self._broken_views:
                _warn(f"ignoring unreadable parse cache {path} ({exc}); it will be rebuilt")
            self._broken_views.add(name)
            return {}

    def stored_view(self, name: str, key: str, digest: str) -> Any:
        """The stored value of view *name* for the note at *key*, if it belongs to *digest*.

        The row is handed over (removed): the note keeps the converted value,
        and holding both would double the memory.
        """
        rows = self._views.get(name)
        if rows is None:
            rows = self._views[name] = self._decode_view(name)
        row = rows.pop(key, None)
        if type(row) is list and len(row) == 2 and row[0] == _view_tag(digest):
            return row[1]
        return _MISSING

    def computed(self, name: str, key: str, value: Any) -> None:
        """Record a view this run computed because the cache did not have it."""
        self._computed.setdefault(name, {})[key] = value

    def _key(self, path: Path) -> str:
        text = os.fspath(path)
        prefix = os.fspath(self.root).rstrip(os.sep) + os.sep
        return text[len(prefix):] if text.startswith(prefix) else text

    def load_all(self, paths: list[Path]) -> list[Note]:
        """Parse *paths* like ``load_note`` would, reusing unchanged entries."""
        if self.digests is not None:
            # A second load in the same command: start from what is on disk now.
            self.dirty = False
            self._read()
        notes: list[Note] = []
        rows: dict[str, list] = {}
        with _no_gc():
            for path in paths:
                key = self._key(path)
                note, record = self._load(path, key)
                notes.append(note)
                if record is not None:
                    signature, digest, settled = record
                    rows[key] = [*signature, digest, settled, [note.front, note.has_front, note.front_error, note.body_offset]]
        if set(self.entries) - set(rows):
            self.dirty = True  # a note was deleted, renamed or could not be recorded
        if self.dirty:
            self._write([(self.file, {"format": FORMAT, "whykit": __version__, "fingerprint": self.fingerprint, "entries": rows})])
        # The notes own their front matter now; keep only what the view files need.
        self.entries = {}
        self.digests = {key: row[4] for key, row in rows.items()}
        return notes

    def _load(self, path: Path, key: str) -> tuple[Note, Record | None]:
        try:
            before = os.stat(path)
        except OSError:
            return load_note(path), None  # raises like an uncached load
        signature = _signature(before)
        entry = self.entries.get(key)
        now = time.time_ns()
        if entry is not None and entry.signature == signature:
            if entry.settled:
                note = self._restore(path, key, entry, entry.digest)
                if note is not None:
                    return note, (signature, entry.digest, True)
                return self._parse(path, key, path.read_bytes(), before, now)
            # Racy or future-dated when it was parsed: verify by content.
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest == entry.digest and _signature(os.stat(path)) == signature:
                note = self._restore(path, key, entry, digest)
                if note is not None:
                    note.text = _normalise(_decode(data))
                    settled = _settled(before, now)
                    if settled:
                        self.dirty = True
                    return note, (signature, digest, settled)
            return self._parse(path, key, data, before, now)
        return self._parse(path, key, path.read_bytes(), before, now)

    def _restore(self, path: Path, key: str, entry: _Entry, digest: str) -> CachedNote | None:
        try:
            front = _thaw_front(entry.fields[0])
        except (_Corrupt, TypeError, ValueError, RecursionError):
            _warn(f"parse cache entry for {path.name} is damaged; parsing the note again")
            return None
        self.hits += 1
        return CachedNote(path, digest, [front, *entry.fields[1:]], self, key)

    def _parse(self, path: Path, key: str, data: bytes, before: os.stat_result, now: int) -> tuple[Note, Record | None]:
        self.misses += 1
        self.dirty = True
        try:
            text = _decode(data)
        except UnicodeDecodeError:
            return load_note(path), None  # raises exactly like an uncached load
        note = load_note(path, text=text)
        try:
            after = os.stat(path)
        except OSError:
            return note, None
        if _signature(after) != _signature(before):
            return note, None  # changed while being read: do not record
        digest = hashlib.sha256(data).hexdigest()
        note.__class__ = FreshNote
        note.cache, note.key, note.digest = self, key, digest  # type: ignore[attr-defined]
        return note, (_signature(before), digest, _settled(before, now))

    # -- writing ---------------------------------------------------------

    def _view_payloads(self) -> Iterator[tuple[Path, dict]]:
        if self.digests is None:
            return
        current = {key: _view_tag(digest) for key, digest in self.digests.items() if key not in self.stale}
        for name in NOTE_FACTS:
            computed = self._computed.get(name, {})
            if not computed and name not in self._broken_views:
                continue
            # Start from what is on disk (decoded again: rows handed to notes
            # are gone from the copy in memory), keep what still matches.
            stored = {} if name in self._broken_views else self._decode_view(name)
            rows: dict[str, list] = {}
            for key, tag in current.items():
                if key in computed:
                    rows[key] = [tag, computed[key]]
                else:
                    row = stored.get(key)
                    if type(row) is list and len(row) == 2 and row[0] == tag:
                        rows[key] = row
            yield self._view_file(name), {"format": FORMAT, "whykit": __version__, "fingerprint": self.fingerprint, "rows": rows}

    def save(self) -> None:
        """Write the view files this run added to or found damaged."""
        self._write(list(self._view_payloads()))

    def _write(self, payloads: list[tuple[Path, dict]]) -> None:
        if not payloads:
            return
        try:
            from .io import _acquire_exclusive, _release_exclusive, safe_vault_dir

            if not _no_symlinks(self.root, CACHE_DIR):
                return
            directory = safe_vault_dir(self.root, CACHE_DIR)
            ignore = directory / ".gitignore"
            if not os.path.lexists(ignore):
                # Older vaults do not ignore the cache; keep it out of Git anyway.
                with contextlib.suppress(FileExistsError):
                    fd = _open_nofollow(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                    with os.fdopen(fd, "w", encoding="utf-8") as out:
                        out.write("# WhyKit parse cache: machine state, safe to delete.\n*\n")
            blobs = [
                (target, json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
                for target, payload in payloads
            ]
        except (OSError, RuntimeError, ValueError):
            return
        with contextlib.ExitStack() as stack:
            try:
                handle = stack.enter_context(os.fdopen(_open_nofollow(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT), "r+b"))
                _acquire_exclusive(handle, non_blocking=True)
                stack.callback(_release_exclusive, handle)
            except OSError:
                return  # another run is writing the cache; its copy is as good
            if self._vault_busy():
                return
            for target, blob in blobs:
                tmp = directory / f".{target.name}.tmp-{secrets.token_hex(6)}"
                try:
                    with open(tmp, "xb") as out:
                        out.write(blob)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(tmp, target)
                except OSError:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                    return

    def _vault_busy(self) -> bool:
        """True while another WhyKit command holds the vault's mutation lock."""
        from .io import _acquire_exclusive, _release_exclusive

        lock = self.root / ".whykit" / "mutation.lock"
        try:
            handle = os.fdopen(_open_nofollow(lock, os.O_RDWR), "r+b")
        except OSError:
            return False
        with handle:
            try:
                _acquire_exclusive(handle, non_blocking=True)
            except OSError:
                return True
            with contextlib.suppress(OSError):
                _release_exclusive(handle)
        return False


# -- scope -------------------------------------------------------------------

class _Scope:
    def __init__(self) -> None:
        self.caches: dict[str, DiskCache | None] = {}

    def cache_for(self, root: Path) -> DiskCache | None:
        key = os.fspath(root)
        if key not in self.caches:
            try:
                self.caches[key] = DiskCache(root)
            except OSError:
                self.caches[key] = None
        return self.caches[key]


_SCOPE: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("whykit_parse_cache", default=None)


def disabled_by_environment() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().lower() not in ("", "0", "false", "no", "off")


def current(root: Path) -> DiskCache | None:
    """The cache for *root* if a :func:`persistent` scope is active."""
    scope = _SCOPE.get()
    return scope.cache_for(root) if scope is not None else None


@contextlib.contextmanager
def persistent(enabled: bool = True) -> Iterator[None]:
    """Use and update the on-disk parse cache for every vault loaded inside.

    Front matter and signatures are written when the vault is loaded; views
    computed along the way when the block finishes without an exception.
    """
    if not enabled or disabled_by_environment() or _SCOPE.get() is not None:
        yield
        return
    scope = _Scope()
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)
    for cache in scope.caches.values():
        if cache is not None:
            cache.save()
