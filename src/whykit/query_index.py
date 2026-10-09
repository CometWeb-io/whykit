"""Disposable SHA-bound candidates; query.py still verifies and ranks every hit."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lint import Note
    from .vault_index import VaultIndex


def _indexable(text: str) -> bool:
    if '\0' in text:
        return False
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


class QueryIndex:
    """One validated visible generation, used under the owning NoteCache lock."""

    def __init__(self) -> None:
        self.connection: sqlite3.Connection | None = None
        self.notes: tuple[Note, ...] = ()
        self.paths: tuple[str, ...] = ()
        self.canonical: object = None
        self.root = ''
        self.version_sha256: str | None = None
        self.unindexed: set[int] = set()
        self.fts = False

    def __del__(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def candidates(self, index: VaultIndex, needle: str, metadata: dict[str, str | bool]) -> list[Note]:
        unchanged = (self.connection is not None and self.root == os.fspath(index.root)
                     and self.canonical is index._canonical and len(self.notes) == len(index.notes)
                     and all(a is b for a, b in zip(self.notes, index.notes, strict=True)))
        if not unchanged:
            self._build(index)
        assert self.connection is not None
        if any(isinstance(value, str) and not _indexable(value) for value in metadata.values()):
            return index.notes
        # Field names come only from query.py, never from request text.
        clauses = [f'{key} = ?' for key in metadata]
        values: list[str | bool] = list(metadata.values())
        if self.fts and len(needle) >= 3 and _indexable(needle):
            # detail=none stores document postings, not positions. A bounded
            # conjunction is a superset; the existing matcher checks the phrase.
            grams = dict.fromkeys(needle[n:n+3] for n in range(0, len(needle)-2, max(1, len(needle)//16)))
            expression = ' AND '.join('"' + gram.replace('"', '""') + '"' for gram in list(grams)[:16])
            clauses.append('ordinal IN (SELECT rowid FROM terms WHERE terms MATCH ?)')
            values.append(expression)
        sql = 'SELECT ordinal FROM metadata' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
        ordinals = self.unindexed | {row[0] for row in self.connection.execute(sql, values)}
        notes = []
        for ordinal in sorted(ordinals):
            note = index.notes[ordinal-1]
            index._relative[note.path] = self.paths[ordinal-1]
            notes.append(note)
        return notes

    def _build(self, index: VaultIndex) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.version_sha256 = None
        self.unindexed = set()
        connection = sqlite3.connect(':memory:', check_same_thread=False)
        digest = hashlib.sha256(b'whykit-query-index-v1\0')
        paths = []
        try:
            connection.execute('PRAGMA temp_store=MEMORY')
            connection.execute('CREATE TABLE metadata(ordinal INTEGER PRIMARY KEY, kind TEXT, state TEXT, label TEXT, canonical INTEGER)')
            for key in ('kind', 'state', 'label', 'canonical'):
                connection.execute(f'CREATE INDEX metadata_{key} ON metadata({key})')
            try:
                connection.execute("CREATE VIRTUAL TABLE terms USING fts5(text, content='', detail=none, tokenize='trigram case_sensitive 1')")
                self.fts = True
            except sqlite3.OperationalError as error:
                if 'no such module: fts5' not in str(error) and 'no such tokenizer: trigram' not in str(error):
                    raise
                self.fts = False
            for ordinal, note in enumerate(index.notes, 1):
                path = index.relative(note.path)
                paths.append(path)
                title = str(note.front.get('title') or note.path.stem)
                fields = [str(note.front.get(key) or '') for key in ('type', 'status', 'sensitivity')]
                canonical = note.front.get('source_of_truth') is True
                bound = json.dumps([path, note.content_sha256, title, fields, canonical], ensure_ascii=True, separators=(',', ':'))
                digest.update(bound.encode('ascii') + b'\n')
                text = '\x1f'.join((title, path, note.text)).casefold()
                if not all(_indexable(value) for value in (*fields, text)):
                    self.unindexed.add(ordinal)
                    continue
                connection.execute('INSERT INTO metadata VALUES (?, ?, ?, ?, ?)', (ordinal, *fields, canonical))
                if self.fts:
                    connection.execute('INSERT INTO terms(rowid, text) VALUES (?, ?)', (ordinal, text))
            connection.commit()
        except BaseException:
            connection.close()
            raise
        self.connection = connection
        self.notes, self.canonical, self.root = tuple(index.notes), index._canonical, os.fspath(index.root)
        self.paths = tuple(paths)
        self.version_sha256 = digest.hexdigest()
