"""Request-scoped vault model: one parse, many consumers."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .lint import Note, _build_index, _resolve, collect_markdown, load_note, rel


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

    @classmethod
    def load(cls, root: Path) -> VaultIndex:
        root = Path(root).resolve()
        notes = [load_note(path) for path in collect_markdown(root, [])]
        return cls(
            root=root,
            notes=notes,
            by_path={note.path.resolve(): note for note in notes},
            link_index=_build_index(notes),
        )

    def note_for(self, path: Path) -> Note | None:
        return self.by_path.get(Path(path).resolve())

    def relative(self, path: Path) -> str:
        cached = self._relative.get(path)
        if cached is None:
            cached = self._relative[path] = rel(self.root, path)
        return cached

    def resolve_link(self, target: str) -> tuple[Path | None, bool]:
        """Resolve a wikilink target exactly like the linter, once per target."""
        cached = self._resolved.get(target)
        if cached is None:
            cached = self._resolved[target] = _resolve(self.root, target, self.link_index)
        return cached
