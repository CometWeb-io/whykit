"""Candidate indexes may reduce work, never change substring search semantics."""
from __future__ import annotations

import tempfile
import gc
import tracemalloc
import sqlite3
import random
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from _vaults import fresh_vault
from whykit.lint import Note
from whykit import lint
from whykit.query import query_vault
from whykit.vault_index import NoteCache, VaultIndex, reuse_notes


class CountedText(str):
    folds = 0

    def casefold(self):
        type(self).folds += 1
        return super().casefold()


class RetrievalIndexTests(unittest.TestCase):
    def test_complete_confined_views_do_not_copy_note_lookup_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            notes = [Note(root / f'n-{n}.md', '', {}) for n in range(1000)]
            index = VaultIndex(root, notes, {n.path: n for n in notes}, {n.path.stem: {n.path} for n in notes},
                               _canonical={n.path: n.path for n in notes})
            index.derived['private'] = object()
            tracemalloc.start()
            try:
                views = [index.subset(lambda _: True) for _ in range(8)]
                gc.collect()
                retained = tracemalloc.get_traced_memory()[0]
            finally:
                tracemalloc.stop()
            self.assertLess(retained, 16 * len(notes) * len(views))
            for view in views:
                self.assertTrue(view.confined)
                self.assertEqual(view.derived, {})
                self.assertIsNot(view._resolved, index._resolved)
            extra = root / 'not-captured.md'
            extra.write_text('# not part of captured notes\n', encoding='utf-8')
            self.assertEqual(views[0].resolve_link('not-captured'), (None, False))

    def test_warm_selective_query_does_not_rescan_every_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            notes = [Note(root / f'n-{n}.md', CountedText(f'common body {n}\n'), {'title': f'Note {n}'}) for n in range(200)]
            notes[-1] = Note(root / 'last.md', CountedText('isolatedmarker\n'), {'title': 'Last'})
            index = VaultIndex(root, notes, {n.path: n for n in notes}, {}, _canonical={n.path: n.path for n in notes})
            cache = NoteCache()
            with reuse_notes(cache):
                expected = query_vault(root, text='isolatedmarker', vault=index)
                CountedText.folds = 0
                actual = query_vault(root, text='isolatedmarker', vault=index)
            self.assertEqual(actual, expected)
            self.assertEqual(actual['total'], 1)
            if cache._query is None or not cache._query.fts:
                self.skipTest('body-scan reduction requires the optional trigram capability')
            self.assertLessEqual(CountedText.folds, 5, f'rescanned {CountedText.folds} bodies')

    def test_warm_ranked_query_reuses_only_validated_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'vault'
            fresh_vault(root, '--minimal')
            for n in range(30):
                (root / f'notes/n-{n}.md').write_text('# commonmarker\n', encoding='utf-8')
            cache = NoteCache()
            cache.racy_ns = 0
            with reuse_notes(cache):
                expected = query_vault(root, text='commonmarker')
                with patch('whykit.vault_index.rel', wraps=lint.rel) as relative:
                    actual = query_vault(root, text='commonmarker')
                self.assertEqual(actual, expected)
                self.assertEqual(relative.call_count, 0)

    def test_missing_trigram_capability_keeps_metadata_and_scan_results(self):
        from whykit.query_index import QueryIndex
        connect = sqlite3.connect
        class MetadataOnly:
            def __init__(self, *args, **kwargs):
                self.inner = connect(*args, **kwargs)
            def execute(self, sql, *args):
                if sql.startswith('CREATE VIRTUAL TABLE'):
                    raise sqlite3.OperationalError('no such tokenizer: trigram')
                return self.inner.execute(sql, *args)
            def commit(self):
                self.inner.commit()
            def close(self):
                self.inner.close()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            notes = [Note(root / 'a.md', 'needle', {'type': 'research'}), Note(root / 'b.md', 'needle', {'type': 'guide'})]
            index = VaultIndex(root, notes, {n.path: n for n in notes}, {})
            with patch('whykit.query_index.sqlite3.connect', MetadataOnly):
                search = QueryIndex()
                self.assertEqual(search.candidates(index, 'needle', {'kind': 'research'}), notes[:1])
                self.assertEqual(search.candidates(index, 'needle', {}), notes)

    def test_generation_sha_binds_text_metadata_and_paths(self):
        from whykit.query_index import QueryIndex
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            versions = []
            for path, text, kind in [('a.md', 'first', 'research'), ('a.md', 'second', 'research'),
                                     ('a.md', 'second', 'guide'), ('b.md', 'second', 'guide')]:
                note = Note(root / path, text, {'type': kind})
                index = VaultIndex(root, [note], {note.path: note}, {})
                search = QueryIndex()
                search.candidates(index, text, {})
                versions.append(search.version_sha256)
            self.assertEqual(len(set(versions)), 4)
            self.assertTrue(all(v is not None and len(v) == 64 for v in versions))

    def test_candidate_supersets_match_full_scan_for_random_substrings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            rng = random.Random(19)
            alphabet = 'abc XYZ\n\t"*[]()%_éßİK😀\0'
            texts = [''.join(rng.choices(alphabet, k=150)) for _ in range(40)]
            texts += ['a' * 3000 + 'longmarker', 'surrogate-\udcff-body']
            notes = [Note(root / f'n-{n}.md', text, {'title': f'Note {n}'}) for n, text in enumerate(texts)]
            index = VaultIndex(root, notes, {n.path: n for n in notes}, {})
            cache = NoteCache()
            needles = ['longmarker', texts[-2], 'surrogate-\udcff', '"""', '\t\n ']
            for _ in range(100):
                text = rng.choice(texts)
                start = rng.randrange(len(text))
                needles.append(text[start:start+rng.randrange(1, 24)])
            for needle in needles:
                with self.subTest(needle=repr(needle)):
                    expected = query_vault(root, text=needle, vault=index)
                    with reuse_notes(cache):
                        actual = query_vault(root, text=needle, vault=index)
                    self.assertEqual(actual, expected)

    def test_failed_rebuild_never_returns_a_partial_or_old_index(self):
        from whykit.query_index import QueryIndex
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            search = QueryIndex()
            first = Note(root / 'a.md', 'oldmarker')
            old = VaultIndex(root, [first], {first.path: first}, {})
            self.assertEqual(search.candidates(old, 'oldmarker', {}), [first])
            second = Note(root / 'b.md', 'newmarker')
            new = VaultIndex(root, [second], {second.path: second}, {})
            with patch('whykit.query_index.sqlite3.connect', side_effect=sqlite3.OperationalError('injected allocation failure')):
                with self.assertRaises(sqlite3.OperationalError):
                    search.candidates(new, 'newmarker', {})
            self.assertEqual(search.candidates(new, 'newmarker', {}), [second])
            self.assertEqual(search.candidates(new, 'oldmarker', {}), [])

    def test_core_queries_work_without_the_optional_sqlite_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'vault'
            fresh_vault(root, '--minimal')
            (root / 'notes/probe.md').write_text('# isolatedmarker\n', encoding='utf-8')
            script = '''
import sys, importlib.abc
from pathlib import Path
class BlockSQLite(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'sqlite3', '_sqlite3'}:
            error = ModuleNotFoundError if sys.argv[2] == 'missing' else ImportError
            raise error('injected missing optional binding', name=fullname)
sys.meta_path.insert(0, BlockSQLite())
from whykit.query import query_vault
from whykit.vault_index import NoteCache, reuse_notes
root=Path(sys.argv[1]); expected=query_vault(root,text='isolatedmarker')
with reuse_notes(NoteCache()):
    for _ in range(2):
        assert query_vault(root,text='isolatedmarker') == expected
assert expected['total'] == 1
'''
            for failure in ('missing', 'load_error'):
                with self.subTest(failure=failure):
                    result = subprocess.run([sys.executable, '-c', script, str(root), failure], capture_output=True, text=True, encoding='utf-8', timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_indexed_results_match_uncached_unicode_literals_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'vault'
            fresh_vault(root, '--minimal')
            texts = ['Straße Kelvin CAFÉ', 'prefix* OR "quoted"', 'first\nsecond', 'xx\0suffix', '😀🌍🚀 e\u0301']
            for n, text in enumerate(texts):
                (root / f'notes/n-{n}.md').write_text(
                    f'---\ntitle: Note {n}\ntype: research\nstatus: draft\nowner: Example Owner\nsensitivity: public\ntags: [odd]\nsource_ids: [E-001]\n---\n{text}\n', encoding='utf-8')
            cache = NoteCache()
            cache.racy_ns = 0
            for text in [None, 's', 'ss', 'STRASSE', 'kelvin', 'CAFÉ', 'prefix* OR "quoted"', 'first\nsecond', '\0suffix', 'suffix', '😀🌍🚀', 'e\u0301']:
                for filters in ({}, {'doc_type': 'research'}, {'status': 'draft', 'owner': 'OWNER'},
                                {'tag': 'ODD', 'source_id': 'E-001'}, {'canonical_only': True}, {'sensitivity': 'public'}):
                    with self.subTest(text=text, filters=filters):
                        expected = query_vault(root, text=text, **filters)
                        with reuse_notes(cache):
                            actual = query_vault(root, text=text, **filters)
                        self.assertEqual(actual, expected)

    def test_edited_deleted_and_renamed_notes_refresh_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'vault'
            fresh_vault(root, '--minimal')
            note = root / 'notes/probe.md'
            note.write_text('# isolatedmarker\n', encoding='utf-8')
            cache = NoteCache()
            cache.racy_ns = 0
            with reuse_notes(cache):
                self.assertEqual(query_vault(root, text='isolatedmarker')['total'], 1)
                note.write_text('# replacementword\n', encoding='utf-8')
                self.assertEqual(query_vault(root, text='isolatedmarker')['total'], 0)
                renamed = note.with_name('replacementword.md')
                note.rename(renamed)
                self.assertEqual(query_vault(root, text='replacementword')['results'][0]['path'], 'notes/replacementword.md')
                renamed.unlink()
                self.assertEqual(query_vault(root, text='replacementword')['total'], 0)
