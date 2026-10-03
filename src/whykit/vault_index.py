"""Request-scoped vault model: one parse, many consumers."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .lint import Note, _build_index, _link_key, _real, _resolve, _within, collect_markdown, load_note, path_cache, rel


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

    @classmethod
    @path_cache()
    def load(cls, root: Path) -> VaultIndex:
        root = Path(root).resolve()
        notes = [load_note(path) for path in collect_markdown(root, [])]
        return cls(
            root=root,
            notes=notes,
            by_path={_real(note.path): note for note in notes},
            link_index=_build_index(notes),
        )

    def subset(self, keep: Callable[[Note], bool]) -> VaultIndex:
        """Return a confined index over the notes for which *keep* is true.

        Every consumer that takes a ``vault=`` argument then behaves as if the
        dropped notes did not exist: they are not link targets, not decision ID
        holders and not graph nodes, so a stem shared with a dropped note
        resolves to the remaining one instead of being reported as ambiguous.
        """
        notes = [note for note in self.notes if keep(note)]
        return VaultIndex(
            root=self.root,
            notes=notes,
            by_path={_real(note.path): note for note in notes},
            link_index=_build_index(notes),
            _relative=self._relative,
            confined=True,
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
        stem = _link_key(normalized.rstrip("/").split("/")[-1].removesuffix(".md"))
        hits = {item for item in self.link_index.get(stem, set()) if _within(self.root, item)}
        if len(hits) == 1:
            return next(iter(hits)), False
        return None, len(hits) > 1
