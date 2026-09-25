"""Request-scoped vault model: one parse, many consumers."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .lint import Note, _build_index, collect_markdown, load_note, rel


@dataclass
class VaultIndex:
    """Immutable in-memory view of a vault after one Markdown parse pass."""

    root: Path
    notes: list[Note]
    by_path: dict[Path, Note] = field(repr=False)
    link_index: dict[str, set[Path]] = field(repr=False)

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
        return rel(self.root, path)
