"""Request-scoped vault model: one parse, many consumers."""
from __future__ import annotations

import contextlib
import contextvars
import os
import hashlib
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .io import vault_read_lock
from .lint import Note, _build_index, _link_key, _real, _resolve, _within, collect_markdown, load_note, _normalise, _request_cache, path_cache, rel, strip_markdown_suffix

if TYPE_CHECKING:
    from .query_index import QueryIndex


class NoteCache:
    """Parsed notes kept across requests while their files are unchanged.

    A long-lived reader (the MCP server) would otherwise read and parse every
    note on every call.  Each entry is keyed by the file's path and checked
    against its current ``stat`` signature (modification and change time,
    size, inode), plus content where change time is untrusted, so an edit, an atomic replace, a rename
    or a deletion is always picked up by the next call.

    A file whose timestamps are within :attr:`racy_ns` of the clock is never
    cached: a second write in the same timestamp tick, with the same size,
    would otherwise be invisible (the "racy clean" problem Git also guards
    against).  Note-derived lookup maps are also reused after validating paths.
    Link resolution, findings and other derived views are computed per request, because they depend on
    files outside the note set (attachments, symlinks, configuration).

    Notes are shared between requests, so callers must treat them as read-only,
    as every WhyKit consumer already does.
    """

    racy_ns = 2_000_000_000

    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[int, ...], Note]] = {}
        self._lock = threading.Lock()
        self._query_lock = threading.Lock()
        self._query: QueryIndex | None = None
        self._query_unavailable = False
        self._shape: tuple[Path, tuple[Note, ...], tuple[Path, ...], dict[Path, Note], dict[str, set[Path]], dict[Path, Path]] | None = None
        self.hits = 0
        self.misses = 0

    def load_all(self, paths: list[Path]) -> list[Note]:
        now = time.time_ns()
        fresh: dict[str, tuple[tuple[int, ...], Note]] = {}
        notes: list[Note] = []
        with self._lock:
            entries = self._entries
        for path in paths:
            key = os.fspath(path)
            try:
                link = os.lstat(path)
                info = os.stat(path) if stat.S_ISLNK(link.st_mode) else link
            except OSError:
                notes.append(load_note(path))  # raises like an uncached load
                continue
            signature = (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino, info.st_dev,
                         link.st_mtime_ns, link.st_ctime_ns, link.st_size, link.st_ino, link.st_dev)
            cached = entries.get(key)
            same = cached is not None and cached[0] == signature
            from .parse_cache import CHANGE_TIME_TRUSTED
            if same and cached is not None and not CHANGE_TIME_TRUSTED:
                try:
                    text = _normalise(path.read_text(encoding="utf-8"))
                    same = hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest() == cached[1].content_sha256
                except UnicodeError:
                    same = False
            if same and cached is not None:
                self.hits += 1
                note = cached[1]
            else:
                self.misses += 1
                note = load_note(path)
            notes.append(note)
            if now - max(info.st_mtime_ns, info.st_ctime_ns) >= self.racy_ns:
                fresh[key] = cached if same and cached is not None else (signature, note)
        # Only the files of this load survive: a deleted note is dropped.
        with self._lock:
            self._entries = fresh
        return notes


    def topology(self, root: Path, notes: list[Note]) -> tuple[dict[Path, Note], dict[str, set[Path]], dict[Path, Path]]:
        """Reuse only note-derived maps; filesystem link resolution stays request-scoped."""
        canonical = tuple(_real(note.path) for note in notes)
        with self._lock:
            previous = self._shape
            if (previous is not None and previous[0] == root and len(previous[1]) == len(notes)
                    and all(os.fspath(a) == os.fspath(b) for a, b in zip(canonical, previous[2], strict=True))
                    and all(a is b for a, b in zip(previous[1], notes, strict=True))):
                return previous[3], previous[4], previous[5]
            by_path = dict(zip(canonical, notes, strict=True))
            links = _build_index(notes)
            canonical_map = dict(zip((note.path for note in notes), canonical, strict=True))
            self._shape = (root, tuple(notes), canonical, by_path, links, canonical_map)
            return by_path, links, canonical_map

    def candidates(self, index: VaultIndex, needle: str, metadata: dict[str, str | bool]) -> list[Note]:
        with self._query_lock:
            if self._query_unavailable:
                return index.notes
            if self._query is None:
                try:
                    from .query_index import QueryIndex
                except ImportError as error:
                    if error.name not in {'sqlite3', '_sqlite3'}:
                        raise
                    self._query_unavailable = True
                    return index.notes
                self._query = QueryIndex()
            return self._query.candidates(index, needle, metadata)


_NOTE_CACHE: contextvars.ContextVar[NoteCache | None] = contextvars.ContextVar("whykit_note_cache", default=None)


@contextlib.contextmanager
def reuse_notes(cache: NoteCache) -> Iterator[None]:
    """Let every :meth:`VaultIndex.load` in this context reuse *cache*."""
    token = _NOTE_CACHE.set(cache)
    try:
        yield
    finally:
        _NOTE_CACHE.reset(token)


@dataclass
class VaultIndex:
    """Immutable in-memory view of a vault after one Markdown parse pass."""

    root: Path
    notes: list[Note]
    by_path: dict[Path, Note] = field(repr=False)
    link_index: dict[str, set[Path]] = field(repr=False)
    # Link resolution and relative paths cost several ``stat``/``realpath``
    # calls each.  A vault repeats the same targets thousands of times, so both
    # are memoised for the lifetime of this request-scoped index.
    _resolved: dict[str, tuple[Path | None, bool]] = field(default_factory=dict, repr=False, compare=False)
    _relative: dict[Path, str] = field(default_factory=dict, repr=False, compare=False)
    # Views derived from this parse (e.g. the wikilink adjacency used by impact
    # analysis), so a multi-record command does not rebuild them per record.
    # A subset() starts with its own empty dict: derived views never cross views.
    derived: dict = field(default_factory=dict, repr=False, compare=False)
    # A confined index resolves links only to its own notes: a file on disk
    # that is not one of them is treated as if it did not exist.  See subset().
    confined: bool = field(default=False, repr=False, compare=False)
    _canonical: dict[Path, Path] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    @path_cache()
    def load(cls, root: Path) -> VaultIndex:
        with vault_read_lock(root):
            root = Path(root).resolve()
            paths = collect_markdown(root, [])
            cache = _NOTE_CACHE.get()
            if cache is not None:
                notes = cache.load_all(paths)
            else:
                from .parse_cache import current

                disk = current(root)
                notes = disk.load_all(paths) if disk is not None else [load_note(path) for path in paths]
            by_path, links, canonical = cache.topology(root, notes) if cache is not None else (
                {_real(note.path): note for note in notes}, _build_index(notes), {}
            )
            request = _request_cache()
            if request is not None:
                # Release the just-validated inventory; keep the shared immutable Paths.
                for path, resolved in canonical.items():
                    key = os.fspath(path)
                    request.realpaths.pop(key, None)
                    request.realpaths[key] = resolved
            return cls(
                root=root,
                notes=notes,
                by_path=by_path,
                link_index=links,
                _canonical=canonical,
            )

    def subset(self, keep: Callable[[Note], bool]) -> VaultIndex:
        """Return a confined index over the notes for which *keep* is true.

        Every consumer that takes a ``vault=`` argument then behaves as if the
        dropped notes did not exist: they are not link targets, not decision ID
        holders and not graph nodes, so a stem shared with a dropped note
        resolves to the remaining one instead of being reported as ambiguous.
        """
        notes = [note for note in self.notes if keep(note)]
        complete = len(notes) == len(self.notes)
        return VaultIndex(
            root=self.root,
            notes=notes,
            by_path=self.by_path if complete else {self._canonical[note.path] if note.path in self._canonical else _real(note.path): note for note in notes},
            link_index=self.link_index if complete else _build_index(notes),
            _relative=self._relative,
            confined=True,
            _canonical=self._canonical,
        )

    def note_for(self, path: Path) -> Note | None:
        return self.by_path.get(_real(Path(path)))

    def relative(self, path: Path) -> str:
        cached = self._relative.get(path)
        if cached is None:
            cached = self._relative[path] = rel(self.root, path)
        return cached

    def resolve_link(self, target: str) -> tuple[Path | None, bool]:
        """Resolve a wikilink target exactly like the linter, once per target."""
        if target.startswith("C-") and target[2:].isascii() and target[2:].isdigit():
            matches = [n.path for n in self.notes if n.front.get("claim_id") == target]
            return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)
        cached = self._resolved.get(target)
        if cached is None:
            cached = self._resolved[target] = (
                self._resolve_confined(target) if self.confined else _resolve(self.root, target, self.link_index)
            )
        return cached

    def _resolve_confined(self, target: str) -> tuple[Path | None, bool]:
        path, ambiguous = _resolve(self.root, target, self.link_index)
        if path is None or self.note_for(path) is not None:
            return path, ambiguous
        # `_resolve` accepted a file that exists on disk but is not one of this
        # index's notes.  Ignore it and fall back to the stem/alias lookup, which
        # only ever sees this index's notes.
        normalized = target.strip().replace("\\", "/").lstrip("/")
        stem = _link_key(strip_markdown_suffix(normalized.rstrip("/").split("/")[-1]))
        hits = {item for item in self.link_index.get(stem, set()) if _within(self.root, item)}
        if len(hits) == 1:
            return next(iter(hits)), False
        return None, len(hits) > 1
