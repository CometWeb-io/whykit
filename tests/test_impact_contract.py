"""Pure M08 producer-contract parsing; no source truth or authority implied."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whykit.impact_contract import parse_impact_note
from whykit.lint import Note

ROOT = Path('/synthetic/vault')
CARD = '00-context/implementation/specs/retry-primary.md'
DIGEST = 'a' * 64
NAMESPACES = {'evidence': 'evidence_raw_sha256', 'claim': 'claim_markdown_sha256',
              'decision': 'decision_record_sha256', 'document': 'document_raw_sha256'}


def dependency(kind='decision', target='D-001', *, pin=True):
    return {'relation': 'implements', 'target': kind + ':' + target,
            'pin': {'namespace': NAMESPACES[kind], 'sha256': DIGEST} if pin else None}


def declaration(kind='spec', dependencies=None):
    return {'format': 'whykit.impact-dependencies/v1', 'artifact_kind': kind, 'dependencies': dependencies or []}


def update_declaration(value, **changes):
    payload = json.loads(value.front['provenance']['whykit_impact'])
    payload.update(changes)
    value.front['provenance']['whykit_impact'] = json.dumps(payload)


def note(path=CARD, *, kind='spec', dependencies=None):
    front = {'title': 'Synthetic card', 'type': 'specification' if kind == 'spec' else 'reference',
             'status': 'draft', 'owner': 'Ada', 'created': '2026-09-17', 'last_updated': '2026-09-17',
             'source_of_truth': False, 'sensitivity': 'restricted',
             'provenance': {'whykit_impact': json.dumps(declaration(kind, dependencies))}}
    return Note(path=ROOT / path, text='Captured bytes only.', front=front, has_front=True)


class ImpactContractTests(unittest.TestCase):
    def invalid(self, value):
        with self.assertRaises(ValueError) as raised:
            parse_impact_note(value, ROOT)
        self.assertEqual(str(raised.exception), 'invalid_impact_record')

    def test_shared_marker_is_shallow_conservative_and_has_no_IO(self):
        from whykit import impact_contract
        values = [note(), note('00-context/Implementation/card.MD')]
        for fields in ({'WhyKit_Impact': None}, {'Provenance': {'whykit-impact': 'bad'}}):
            value = note('Home.md')
            value.front.pop('provenance')
            value.front.update(fields)
            values.append(value)
        ordinary = note('Home.md')
        ordinary.front['provenance'] = {'artifact_kind': 'task', 'external': {'whykit_impact': 'not immediate'}}
        ordinary.front['provenance']['cycle'] = ordinary.front['provenance']
        with patch.object(Path, 'resolve', side_effect=AssertionError('resolve')), \
             patch.object(Path, 'open', side_effect=AssertionError('open')), \
             patch.object(hashlib, 'sha256', side_effect=AssertionError('hash')):
            for value in values:
                self.assertTrue(impact_contract.is_impact_note(value))
            self.assertFalse(impact_contract.is_impact_note(ordinary))

    def test_marker_aliases_keep_foreign_values_opaque_without_repeated_root_normalization(self):
        from whykit import impact_contract
        foreign = {}
        foreign['cycle'] = foreign
        front = {'title': 'Unrelated', 'Provenance': {'WHYKiT-\tIMPACT': None,
                 'external': {'whykit_impact': 'foreign, never traversed'}, 'cycle': foreign},
                 'WhyKit_Impact': 'malformed still marked', 'external': foreign}
        with patch.object(impact_contract, '_key', wraps=impact_contract._key) as normalized:
            markers = impact_contract._markers(front)
        self.assertEqual(markers, [('Provenance', 'WHYKiT-\tIMPACT'), ('WhyKit_Impact',)])
        # At most one normalization per root/immediate-provenance key; repeated
        # work here is multiplied by classification and graph projection passes.
        self.assertLessEqual(normalized.call_count, len(front) + len(front['Provenance']))

    def test_open_third_party_provenance_is_not_a_WhyKit_marker(self):
        cycle = {}
        cycle['external'] = cycle
        deep = {'artifact_kind': 'task'}
        for _ in range(3000):
            deep = {'external': deep}
        for label, metadata in (('generic_kind', {'artifact_kind': 'task'}),
                                ('generic_dependencies', {'impact_dependencies': 'third-party schema'}),
                                ('deep_external', {'external': deep}), ('cyclic_external', {'external': cycle}),
                                ('large_external', {'external': [None] * 5000}),
                                ('nested_namespace_is_external', {'external': {'whykit_impact': 'not an immediate producer marker'}})):
            with self.subTest(case=label):
                value = note('Home.md')
                value.front['provenance'] = metadata
                self.assertIsNone(parse_impact_note(value, ROOT))

    def test_namespaced_marker_malformed_and_aliases_refuse(self):
        for metadata in ({'whykit_impact': None}, {'WhyKit_Impact': '[]'},
                         {'provenance': {'whykit_impact': 'not JSON'}},
                         {'Provenance': {'whykit_impact': '[]'}}):
            value = note('Home.md')
            value.front.pop('provenance')
            value.front.update(metadata)
            self.invalid(value)

    def test_three_card_roles_and_preserved_nested_paths(self):
        for role, folder in [('spec', 'specs'), ('pull-request', 'pull-requests'), ('task', 'tasks')]:
            with self.subTest(role=role):
                value = note('00-context/implementation/' + folder + '/nested-01/retry.md', kind=role,
                             dependencies=[dependency(pin=False)])
                value.front['provenance']['source_identity_authenticated'] = False
                before = copy.deepcopy(value.front)
                result = parse_impact_note(value, ROOT)
                self.assertEqual(result, {'path': value.path.relative_to(ROOT).as_posix(), 'artifact_kind': role,
                                          'dependencies': (dependency(pin=False),)})
                result['dependencies'][0]['target'] = 'decision:D-002'
                self.assertEqual(value.front, before)

    def test_unmarked_ordinary_and_native_records_are_none(self):
        for kind in ('reference', 'decision', 'claim', 'outcome'):
            value = note('Home.md')
            value.front['type'] = kind
            value.front['provenance'] = {'human_reviewed': True, 'other': {'unrelated': 'metadata'}}
            self.assertIsNone(parse_impact_note(value, ROOT))
        value.front['other'] = {'artifact_kind': 'unrelated producer content'}
        self.assertIsNone(parse_impact_note(value, ROOT))

    def test_unrelated_producer_values_are_ignored_and_pin_truth_is_not_inferred(self):
        value = note()
        value.front['provenance']['unrelated'] = [None] * 5000
        value.front['provenance']['cycle'] = value.front['provenance']
        value.front['provenance']['artifact_kind'] = 'foreign producer role'
        parse_impact_note(value, ROOT)
        value = note(dependencies=[dependency('document', CARD), dependency('document', 'missing.md')])
        value.front.update(status='superseded', created='2099-01-01', last_updated='2099-01-01')
        # Self cycles, nonexistent targets, arbitrary syntactic pin bytes and
        # future/historical metadata are reader/assessment concerns, not syntax.
        self.assertEqual(len(parse_impact_note(value, ROOT)['dependencies']), 2)

    def test_real_native_fixture_base_uses_this_exact_contract(self):
        fixture = Path(__file__).parent / 'fixtures/impact'
        spec = importlib.util.spec_from_file_location('impact_parser_native_fixture', fixture / 'generate.py')
        assert spec and spec.loader
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        oracle = json.loads((fixture / 'oracle-v1.json').read_text(encoding='utf-8'))
        from whykit.lint import load_note
        with tempfile.TemporaryDirectory(prefix='whykit-contract-native-') as temporary:
            root = Path(temporary).resolve() / 'vault'
            native.make_base(root, oracle, 'A', native.NativeWriter())
            records = [parse_impact_note(load_note(path), root) for path in root.rglob('*.md')]
            self.assertEqual(sum(record is not None for record in records), 16)
            self.assertEqual(sum(len(record['dependencies']) for record in records if record is not None), 16)

    def test_real_canonical_D_allows_legacy_ID_and_existing_label(self):
        value = note('06-decisions/d-001-choice.MD', dependencies=[dependency('claim', 'C-001')])
        value.front.update(type='decision', sensitivity='public', aliases=['D-001'])
        update_declaration(value, artifact_kind=None)
        result = parse_impact_note(value, ROOT)
        self.assertIsNone(result['artifact_kind'])
        self.assertEqual(result['path'], '06-decisions/d-001-choice.MD')
        for own_id in ('D-000', 'D-0001', 'D-002'):
            invalid = copy.deepcopy(value)
            invalid.front.update(decision_id=own_id, aliases=[])
            self.invalid(invalid)

    def test_all_namespaces_relations_and_null_pin(self):
        values = [dependency('evidence', 'E-001'), dependency('claim', 'C-1000'), dependency(),
                  dependency('document', 'Home.md'), dependency(pin=False)]
        for index, value in enumerate(values):
            value['relation'] = ('requires', 'implements', 'measures')[index % 3]
        self.assertEqual(len(parse_impact_note(note(dependencies=values), ROOT)['dependencies']), 5)

    def test_general_document_targets_preserve_exact_unicode_spaces_and_case(self):
        for target in ('Home.md', 'café note.md', 'Docs/Case Sensitive.MD', '日本語/仕様.md', 'name:part.md', 'é.md', 'e\u0301.md'):
            with self.subTest(target=target):
                result = parse_impact_note(note(dependencies=[dependency('document', target)]), ROOT)
                self.assertEqual(result['dependencies'][0]['target'], 'document:' + target)
        for target in ('00-context/implementation/specs/NONCANONICAL.md', '00-context/Implementation/specs/canonical.md',
                       '00-context/implementation/specs/canonical.MD', '00-context/implementation/specs/café.md'):
            self.invalid(note(dependencies=[dependency('document', target)]))

    def test_target_path_escape_URL_alias_and_control_negatives(self):
        for target in ('/absolute.md', '../outside.md', './Home.md', 'a/../Home.md', 'a/./Home.md', 'a//Home.md',
                       'a\\Home.md', 'C:/Home.md', 'C:Home.md', '//host/Home.md', 'https://source.example/a.md',
                       'Home.md#section', 'Home', 'foo.txt', 'a/\x00.md', 'a/\x85.md', '\ud800.md'):
            with self.subTest(target=repr(target)):
                self.invalid(note(dependencies=[dependency('document', target)]))

    def test_IDs_positive_canonical_bounded_and_not_aliases(self):
        for prefix in ('evidence', 'claim', 'decision'):
            letter = prefix[0].upper()
            for suffix in ('000', '0001', '1', '001-extra', '001 ', '001#x', '١٢٣', '9' * 16):
                self.invalid(note(dependencies=[dependency(prefix, letter + '-' + suffix)]))
            valid = letter + '-' + '9' * 15
            parse_impact_note(note(dependencies=[dependency(prefix, valid)]), ROOT)

    def test_scalar_UTF8_and_reference_boundaries(self):
        for size in (0, 32):
            value = note(dependencies=[dependency()] * size)
            self.assertEqual(len(parse_impact_note(value, ROOT)['dependencies']), size)
        self.invalid(note(dependencies=[dependency()] * 33))
        value = note()
        scalar = value.front['provenance']['whykit_impact']
        value.front['provenance']['whykit_impact'] = scalar + ' ' * (16384 - len(scalar.encode()))
        parse_impact_note(value, ROOT)
        value.front['provenance']['whykit_impact'] += ' '
        self.invalid(value)
        value.front['provenance']['whykit_impact'] = json.dumps(declaration(dependencies=[dependency('document', 'é' * 200 + '.md')] * 32), ensure_ascii=False)
        self.assertLess(len(value.front['provenance']['whykit_impact']), 16384)
        self.assertGreater(len(value.front['provenance']['whykit_impact'].encode('utf-8')), 16384)
        self.invalid(value)
        path = 'a' * (512 - len('document:') - len('.md')) + '.md'
        parse_impact_note(note(dependencies=[dependency('document', path)]), ROOT)
        self.invalid(note(dependencies=[dependency('document', 'a' + path)]))
        path = '00-context/implementation/' + 'a' * 64 + '.md'
        parse_impact_note(note(path), ROOT)
        self.invalid(note('00-context/implementation/' + 'a' * 65 + '.md'))

    def test_strict_shapes_duplicate_keys_and_nonfinite_always_fixed_error(self):
        prefix = '{"format":"whykit.impact-dependencies/v1","artifact_kind":"spec","dependencies":'
        arrays = ['[true]', '[[]]', '[NaN]', '[1e400]',
                  '[{"relation":"implements","relation":"requires","target":"decision:D-001","pin":null}]',
                  '[{"relation":"requires","target":"decision:D-001","pin":{"namespace":"decision_record_sha256","sha256":"' + DIGEST + '","sha256":"' + DIGEST + '"}}]',
                  '[' * 2000 + ']' * 2000]
        scalar_cases = [None, [], {}, True, 1, '{}', 'null', '[]',
                        '{"format":"whykit.impact-dependencies/v1","format":"whykit.impact-dependencies/v1","artifact_kind":"spec","dependencies":[]}']
        scalar_cases.extend(prefix + array + '}' for array in arrays)
        for scalar in scalar_cases:
            value = note()
            value.front['provenance']['whykit_impact'] = scalar
            self.invalid(value)
        for key, replacement in [('relation', 'related'), ('target', True), ('pin', True), ('pin', {}), ('pin', {'namespace': 'document_raw_sha256', 'sha256': DIGEST})]:
            entry = dependency()
            entry[key] = replacement
            self.invalid(note(dependencies=[entry]))
        for entry in ({'relation': 'requires', 'target': 'decision:D-001'}, {**dependency(), 'extra': 1}):
            self.invalid(note(dependencies=[entry]))
        for sha in ('A' * 64, 'sha256:' + DIGEST, 'a' * 63, 1, None):
            entry = dependency()
            entry['pin']['sha256'] = sha
            self.invalid(note(dependencies=[entry]))

    def test_namespaced_envelope_requires_exact_format_keys_and_kind(self):
        for field in ('format', 'artifact_kind', 'dependencies'):
            payload = declaration()
            payload.pop(field)
            value = note()
            value.front['provenance']['whykit_impact'] = json.dumps(payload)
            self.invalid(value)
        for changes in ({'format': 'third-party/v1'}, {'extra': True}, {'artifact_kind': None},
                        {'artifact_kind': True}, {'dependencies': {}}, {'dependencies': None}):
            value = note()
            update_declaration(value, **changes)
            self.invalid(value)

    def test_marked_alias_nested_and_dual_native_records_never_ordinary(self):
        for path in ('00-context/Implementation/specs/a.md', '00-context/implementation/specs/A.md',
                     '00-context/implementation/specs/a.MD', '00-context/implementation/.hidden.md',
                     '00-context/implementation/a_b.md', '00-context\\implementation\\a.md', '../00-context/implementation/a.md'):
            self.invalid(note(path))
        for marker in ({'WhyKit_Impact': 'invalid'}, {'provenance': {'WhyKit_Impact': 'invalid'}},
                       {'Provenance': {'whykit_impact': 'invalid'}}, {'provenance': {'whykit-impact': 'invalid'}}):
            value = note('ordinary.md')
            value.front.pop('provenance')
            value.front.update(marker)
            self.invalid(value)
        for key in ('claim_id', 'outcome_id'):
            value = note()
            value.front[key] = 'C-001' if key == 'claim_id' else 'O-001'
            self.invalid(value)
        for path in ('00-context/claims/c-001.md', '00-context/outcomes/o-001.md'):
            self.invalid(note(path))
        for key in ('WhyKit_Impact', 'whykit-impact'):
            value = note()
            value.front['provenance'][key] = '[]'
            self.invalid(value)

    def test_complete_frontmatter_and_holder_shape(self):
        for key in ('title', 'type', 'status', 'owner', 'created', 'last_updated', 'source_of_truth', 'sensitivity'):
            value = note()
            value.front.pop(key)
            self.invalid(value)
        for key, bad in [('title', ''), ('owner', None), ('status', 'bogus'), ('source_of_truth', 1),
                         ('created', '20260917'), ('created', '2026-02-30'), ('created', dt.date(2026, 9, 17)),
                         ('last_updated', None), ('sensitivity', 'public'), ('type', 'reference')]:
            value = note()
            value.front[key] = bad
            self.invalid(value)
        for mutate in (lambda n: setattr(n, 'front_error', 'malformed'), lambda n: setattr(n, 'has_front', False),
                       lambda n: n.front['provenance'].pop('whykit_impact'),
                       lambda n: update_declaration(n, artifact_kind='unsupported'),
                       lambda n: n.front.update(whykit_impact='invalid')):
            value = note()
            mutate(value)
            self.invalid(value)
        self.invalid(note('ordinary.md'))
        value = note()
        value.front['provenance']['loop'] = value.front['provenance']
        parse_impact_note(value, ROOT)

    def test_no_FS_hash_index_or_symlink_resolution(self):
        # The caller supplied captured metadata. Even an actual symlink pathname is
        # treated lexically; this parser must neither follow nor validate the disk.
        with tempfile.TemporaryDirectory(prefix='whykit-contract-') as temporary:
            root = Path(temporary)
            link = root / CARD
            link.parent.mkdir(parents=True)
            target = root / 'outside.md'
            target.write_text('untrusted disk B', encoding='utf-8')
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                pass
            value = note(dependencies=[dependency('document', 'café note.MD')])
            value.path = link
            # Preload lazy house helpers before denying every vault I/O seam.
            import whykit.artifact_store  # noqa: F401
            import whykit.knowledge_visibility  # noqa: F401
            with patch.object(Path, 'resolve', side_effect=AssertionError('resolve')), \
                 patch.object(Path, 'stat', side_effect=AssertionError('stat')), \
                 patch.object(Path, 'open', side_effect=AssertionError('open')), \
                 patch.object(Path, 'is_symlink', side_effect=AssertionError('is_symlink')), \
                 patch.object(hashlib, 'sha256', side_effect=AssertionError('hash')), \
                 patch('whykit.vault_index.VaultIndex.load', side_effect=AssertionError('reload')):
                result = parse_impact_note(value, root)
                self.assertEqual(result['path'], CARD)
                decision = note('06-decisions/d-001-choice.md')
                decision.path = root / '06-decisions/d-001-choice.md'
                decision.front.update(type='decision', decision_id='D-001')
                update_declaration(decision, artifact_kind=None)
                self.assertIsNone(parse_impact_note(decision, root)['artifact_kind'])


if __name__ == '__main__':
    unittest.main()
