"""Optimised collection and cached queries must answer like fresh filesystem reads."""
from __future__ import annotations

import concurrent.futures
import gc
import os
import random
import subprocess
import sys
import tempfile
import threading
import tracemalloc
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from _vaults import fresh_vault
from whykit import lint
from whykit.mcp_server import VaultTools
from whykit.query import query_vault
from whykit.vault_index import NoteCache, VaultIndex, reuse_notes


class CollectionTests(unittest.TestCase):
    def test_benchmark_rejects_missing_source_instead_of_measuring_another_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/bench_query.py'),
                                     '--src', str(Path(tmp) / 'missing'), '--notes', '1', '--clients', '1', '--runs', '1'],
                                    capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('--src must contain the WhyKit package', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_inventory_order_matches_reference_and_prunes_skipped_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name in ('Z.MD', 'a.md', 'nested/b.Md', '.git/hidden.md', 'node_modules/deep/a.md', 'tests/no.md', 'plain.txt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# fixture\n', encoding='utf-8')
            expected = sorted(p for p in lint.iter_markdown(root) if p.is_file()
                              and not any(part in lint.CONTENT_SKIP_DIRS for part in p.relative_to(root).parts))
            visited = []
            scan = os.scandir
            def counting(path):
                visited.append(Path(path))
                return scan(path)
            with patch('whykit.lint.os.scandir', side_effect=counting), lint.path_cache():
                actual = lint.collect_markdown(root, [])
                self.assertEqual(actual, expected)
                self.assertTrue(all(lint._real(p) == p.resolve() for p in actual))
            self.assertNotIn(root / 'node_modules', visited)
            self.assertNotIn(root / '.git', visited)
            # Explicit scopes retain their existing ability to inspect skipped content.
            self.assertEqual(lint.collect_markdown(root, ['tests']), [root / 'tests/no.md'])

    @unittest.skipIf(os.name == 'nt', 'symlink creation requires privileges on Windows')
    def test_unreadable_symlink_target_fails_instead_of_returning_partial_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, blocked = base / 'vault', base / 'blocked'
            root.mkdir()
            blocked.mkdir()
            (blocked / 'outside.md').write_text('# fixture\n', encoding='utf-8')
            (root / 'a.md').symlink_to(blocked / 'outside.md')
            (root / 'b.md').write_text('# visible\n', encoding='utf-8')
            blocked.chmod(0)
            try:
                if os.access(blocked, os.R_OK):
                    self.skipTest('process can bypass filesystem permissions')
                with self.assertRaises(PermissionError):
                    lint.collect_markdown(root, [])
            finally:
                blocked.chmod(0o700)

    @unittest.skipIf(os.name == 'nt', 'symlink creation requires privileges on Windows')
    def test_symlinks_stay_confined_and_directory_links_are_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / 'vault'
            root.mkdir()
            (root / 'good.md').write_text('# inside\n', encoding='utf-8')
            (base / 'outside.md').write_text('# outside\n', encoding='utf-8')
            (root / 'alias.MD').symlink_to(root / 'good.md')
            (root / 'escape.md').symlink_to(base / 'outside.md')
            (root / 'dir').symlink_to(base, target_is_directory=True)
            (root / 'broken.md').symlink_to(root / 'absent.md')
            self.assertEqual(lint.collect_markdown(root, []), [root / 'alias.MD', root / 'good.md'])


class TopologyTests(unittest.TestCase):
    def test_warm_read_does_not_retain_copies_of_unchanged_stat_rows(self):
        from synthetic_vault import generate
        with tempfile.TemporaryDirectory() as tmp:
            root = generate(Path(tmp) / 'vault', 1000)
            cache = NoteCache()
            cache.racy_ns = 0
            with reuse_notes(cache):
                VaultIndex.load(root)
                VaultIndex.load(root)
                tracemalloc.start()
                try:
                    view = VaultIndex.load(root)
                    gc.collect()
                    retained = tracemalloc.get_traced_memory()[0]
                finally:
                    tracemalloc.stop()
            budget = 200 * len(view.notes)
            self.assertLess(retained, budget, f'warm read retained {retained} bytes; budget {budget}')

    def test_active_read_scopes_do_not_each_retain_a_new_path_inventory(self):
        from synthetic_vault import generate
        with tempfile.TemporaryDirectory() as tmp:
            root = generate(Path(tmp) / 'vault', 1000)
            cache = NoteCache()
            cache.racy_ns = 0
            with reuse_notes(cache):
                VaultIndex.load(root)
                VaultIndex.load(root)
            ready = threading.Barrier(9)
            release = threading.Event()
            def read():
                with reuse_notes(cache), lint.path_cache():
                    view = VaultIndex.load(root)
                    ready.wait(timeout=30)
                    release.wait(timeout=30)
                    return view
            tracemalloc.start()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(read) for _ in range(8)]
                    try:
                        ready.wait(timeout=30)
                        gc.collect()
                        retained = tracemalloc.get_traced_memory()[0]
                    finally:
                        release.set()
                    views = [future.result() for future in futures]
            finally:
                tracemalloc.stop()
            budget = 200 * len(views[0].notes) * len(views)
            self.assertLess(retained, budget, f'active scopes retained {retained} bytes; budget {budget}')

    def test_repeated_validated_views_do_not_retain_another_inventory_per_view(self):
        from synthetic_vault import generate
        with tempfile.TemporaryDirectory() as tmp:
            root = generate(Path(tmp) / 'vault', 1000)
            cache = NoteCache()
            cache.racy_ns = 0
            with reuse_notes(cache):
                VaultIndex.load(root)
                VaultIndex.load(root)  # prime content verification on Windows, too
                tracemalloc.start()
                try:
                    views = [VaultIndex.load(root)]
                    gc.collect()
                    before = tracemalloc.get_traced_memory()[0]
                    views.extend(VaultIndex.load(root) for _ in range(8))
                    gc.collect()
                    growth = tracemalloc.get_traced_memory()[0] - before
                finally:
                    tracemalloc.stop()
            # Each extra view needs its note-reference list, not another tree of Paths.
            budget = 12 * len(views[0].notes) * len(views)
            self.assertLess(growth, budget, f'{len(views)} stable views retained {growth} bytes; budget {budget}')

    def test_canonical_spelling_change_is_seen_even_when_windows_paths_compare_equal(self):
        root = Path('/vault')
        note = lint.load_note(root / 'alias.md', text='# unchanged\n')
        cache = NoteCache()
        first = PureWindowsPath('C:/vault/Original.txt')
        second = PureWindowsPath('C:/vault/original.txt')
        self.assertEqual(first, second)
        with patch('whykit.vault_index._real', return_value=first):
            cache.topology(root, [note])
        with patch('whykit.vault_index._real', return_value=second):
            by_path, _, canonical = cache.topology(root, [note])
        self.assertEqual(os.fspath(canonical[note.path]), os.fspath(second))
        self.assertEqual(os.fspath(next(iter(by_path))), os.fspath(second))

    def test_only_note_maps_are_reused_and_file_mutations_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            note = root / 'a.md'
            note.write_text('---\naliases: [before]\n---\n# alpha\n', encoding='utf-8')
            cache = NoteCache()
            cache.racy_ns = 0
            def read():
                with reuse_notes(cache):
                    return VaultIndex.load(root)
            first, second = read(), read()
            self.assertIs(first.by_path, second.by_path)
            self.assertIs(first.link_index, second.link_index)
            self.assertIsNot(first.derived, second.derived)
            self.assertIsNot(first._resolved, second._resolved)
            note.write_text('---\naliases: [after]\n---\n# beta\n', encoding='utf-8')
            changed = read()
            self.assertNotIn('before', changed.link_index)
            self.assertIn('after', changed.link_index)
            note.rename(root / 'renamed.md')
            renamed = read()
            self.assertNotIn(note, renamed.by_path)
            (root / 'added.MD').write_text('# added\n', encoding='utf-8')
            self.assertEqual(len(read().notes), 2)
            (root / 'renamed.md').unlink()
            self.assertEqual([n.path.name for n in read().notes], ['added.MD'])

    @unittest.skipIf(os.name == 'nt', 'symlink creation requires privileges on Windows')
    def test_intermediate_symlink_retarget_to_hardlink_changes_canonical_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            a, b = root / 'a.txt', root / 'b.txt'
            a.write_text('# same inode\n', encoding='utf-8')
            os.link(a, b)
            chain = root / 'chain.link'
            chain.symlink_to(a)
            note = root / 'alias.md'
            note.symlink_to(chain)
            cache = NoteCache()
            cache.racy_ns = 0
            with reuse_notes(cache):
                first = VaultIndex.load(root)
                chain.unlink()
                chain.symlink_to(b)
                second = VaultIndex.load(root)
            self.assertIs(first.notes[0], second.notes[0])
            self.assertIn(a, first.by_path)
            self.assertNotIn(a, second.by_path)
            self.assertIn(b, second.by_path)

    def test_untrusted_change_time_verifies_content_even_with_identical_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / 'a.md'
            note.write_text('# alpha\n', encoding='utf-8')
            cache = NoteCache()
            cache.racy_ns = 0
            original = note.lstat()
            with patch('whykit.vault_index.os.lstat', return_value=original), patch('whykit.parse_cache.CHANGE_TIME_TRUSTED', False):
                first = cache.load_all([note])[0]
                note.write_text('# bravo\n', encoding='utf-8')
                second = cache.load_all([note])[0]
            self.assertIsNot(first, second)
            self.assertIn('bravo', second.text)


class QueryCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / 'vault'
        fresh_vault(self.root, '--minimal')
        self.cache = NoteCache()
        self.cache.racy_ns = 0
        words = ['Straße Kelvin CAFÉ', 'exact "quote" OR prefix*', 'line\nbreak abc', 'xx\0suffix', '😀🌍🚀 e\u0301']
        randomizer = random.Random(17)
        self.needles = ['a', 'ab', 'abc', ' ', 'straße', 'kelvin', 'CAFÉ', '"quote"', 'OR prefix*', 'xx\0suf', 'suffix', '😀🌍🚀', 'e\u0301']
        for number in range(30):
            body = randomizer.choice(words) + '\n' + ''.join(randomizer.choice('abcd!? "é🌍') for _ in range(40))
            for start in range(0, len(body), 9):
                self.needles.append(body[start:start+5])
            path = self.root / 'notes' / f'probe-{number}.md'
            path.write_text(f'---\ntitle: "Probe {number}"\nowner: "Anna Example"\ntype: decision\nstatus: {"template" if number % 3 == 0 else "approved"}\nsensitivity: {"restricted" if number % 2 else "public"}\ntags: [one, two]\nsource_ids: [E-001]\nsource_of_truth: true\n---\n{body}\n', encoding='utf-8')

    def cached(self, **kwargs):
        with reuse_notes(self.cache):
            return query_vault(self.root, **kwargs)

    def test_substrings_and_filters_match_uncached_full_scan(self):
        for needle in self.needles:
            with self.subTest(needle=repr(needle)):
                self.assertEqual(self.cached(text=needle), query_vault(self.root, text=needle))
        for filters in ({'doc_type': 'decision'}, {'status': 'template'}, {'owner': 'ANNA'}, {'tag': 'TWO'},
                        {'source_id': 'E-001'}, {'sensitivity': 'public'}, {'allowed_sensitivities': {'public'}},
                        {'allowed_sensitivities': set()}, {'canonical_only': True}, {'limit': 0}):
            with self.subTest(filters=filters):
                self.assertEqual(self.cached(text='probe', **filters), query_vault(self.root, text='probe', **filters))

    def test_policy_changes_and_parallel_queries_never_return_hidden_records(self):
        tools = VaultTools(self.root, max_sensitivity='public')
        tools.notes.racy_ns = 0
        expected = tools.query(text='probe')
        self.assertTrue(all(r['sensitivity'] == 'public' for r in expected['results']))
        note = self.root / 'notes/probe-0.md'
        note.write_text(note.read_text(encoding='utf-8').replace('sensitivity: public', 'sensitivity: restricted'), encoding='utf-8')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            reports = list(pool.map(lambda _: tools.query(text='probe'), range(16)))
        for report in reports:
            self.assertEqual(report['total'], expected['total'] - 1)
            self.assertNotIn('notes/probe-0.md', [r['path'] for r in report['results']])


if __name__ == '__main__':
    unittest.main()
