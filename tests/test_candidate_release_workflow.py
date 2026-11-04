"""Exercise the pre-tag workflow's candidate validation with real Git history."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'candidate-check.yml'
IDENTITY = {
    'GIT_AUTHOR_NAME': 'Example Maintainer',
    'GIT_AUTHOR_EMAIL': 'maintainer@example.com',
    'GIT_COMMITTER_NAME': 'Example Maintainer',
    'GIT_COMMITTER_EMAIL': 'maintainer@example.com',
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
}


@unittest.skipIf(os.name == 'nt', 'The validation step runs in bash on an Ubuntu runner')
class CandidateValidationTests(unittest.TestCase):
    def test_workflow_checks_out_only_the_reviewed_candidate(self) -> None:
        import re

        text = WORKFLOW.read_text(encoding='utf-8')
        candidate = '34c24d0a24a499a1cd16f0e1ad4c9ef0c985751d'
        self.assertNotIn('inputs.candidate_sha', text)
        self.assertIn(f'CANDIDATE_SHA: {candidate}', text)
        refs = re.findall(r'^\s+ref: ([0-9a-f]{40})\s*$', text, re.MULTILINE)
        self.assertEqual(refs, [candidate] * 5)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.remote = self.root / 'origin.git'
        self.repo.mkdir()
        self.git('init', '--bare', '-q', str(self.remote), cwd=self.root)
        self.git('init', '-q', '-b', 'main')
        (self.repo / 'src' / 'whykit').mkdir(parents=True)
        (self.repo / 'src' / 'whykit' / '__init__.py').write_text('__version__ = "0.3.4"\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'Candidate')
        self.candidate = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'later.txt').write_text('later\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'Later main')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-q', '-u', 'origin', 'main')
        self.git('switch', '-q', '--orphan', 'outside')
        (self.repo / 'outside.txt').write_text('outside\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'Outside')
        self.outside = self.git('rev-parse', 'HEAD').strip()
        self.git('switch', '-q', 'main')
        text = WORKFLOW.read_text(encoding='utf-8')
        candidate_step = text.split('      - id: candidate\n', 1)[1].split('\n  tests:', 1)[0]
        self.script = textwrap.dedent(candidate_step.split('        run: |\n', 1)[1])

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(['git', *args], cwd=cwd or self.repo, env={**os.environ, **IDENTITY},
                              check=True, text=True, capture_output=True).stdout

    def validate(self, sha: str, version: str, wheel: str = 'a' * 64, sdist: str = 'b' * 64) -> tuple[int, str]:
        output = self.root / 'output'
        output.write_text('', encoding='utf-8')
        summary = self.root / 'summary'
        summary.write_text('', encoding='utf-8')
        env = {**os.environ, **IDENTITY, 'CANDIDATE_SHA': sha, 'EXPECTED_VERSION': version,
               'EXPECTED_WHEEL_SHA256': wheel, 'EXPECTED_SDIST_SHA256': sdist,
               'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary)}
        result = subprocess.run(['bash', '-c', self.script], cwd=self.repo, env=env,
                                text=True, capture_output=True)
        return result.returncode, output.read_text(encoding='utf-8')

    def test_accepts_exact_ancestor_and_matching_version(self) -> None:
        code, output = self.validate(self.candidate, '0.3.4')
        self.assertEqual(code, 0)
        self.assertEqual(output, f'sha={self.candidate}\n')

    def test_rejects_bad_inputs_non_ancestor_and_existing_tag(self) -> None:
        self.git('tag', '-a', 'other-tag', '-m', 'Annotation', self.candidate)
        tag_object = self.git('rev-parse', 'other-tag').strip()
        for sha, version in [('a' * 39, '0.3.4'), ('f' * 40, '0.3.4'),
                             (self.candidate, '0.3.5'), (self.candidate, '0.3.4;echo bad'),
                             (self.outside, '0.3.4'), (tag_object, '0.3.4')]:
            with self.subTest(sha=sha, version=version):
                code, output = self.validate(sha, version)
                self.assertNotEqual(code, 0)
                self.assertEqual(output, '')
        self.git('tag', 'v0.3.4', self.candidate)
        code, output = self.validate(self.candidate, '0.3.4')
        self.assertNotEqual(code, 0)
        self.assertEqual(output, '')

    def test_rejects_invalid_artifact_digests(self) -> None:
        for wheel, sdist in [('g' * 64, 'b' * 64), ('a' * 64, 'b' * 63), ('$(echo bad)', 'b' * 64)]:
            with self.subTest(wheel=wheel, sdist=sdist):
                code, output = self.validate(self.candidate, '0.3.4', wheel, sdist)
                self.assertNotEqual(code, 0)
                self.assertEqual(output, '')

    def test_distribution_digest_checks_reject_mismatches(self) -> None:
        text = WORKFLOW.read_text(encoding='utf-8')
        block = text.split('          wheels=(dist/*.whl)\n', 1)[1].split(
            '          uv run --locked --only-group dist twine', 1,
        )[0]
        script = 'set -euo pipefail\nwheels=(dist/*.whl)\n' + textwrap.dedent(block)
        dist = self.root / 'dist'
        dist.mkdir()
        (dist / 'candidate.whl').write_bytes(b'wheel')
        (dist / 'candidate.tar.gz').write_bytes(b'sdist')
        wheel = hashlib.sha256(b'wheel').hexdigest()
        sdist = hashlib.sha256(b'sdist').hexdigest()
        for expected_wheel, expected_sdist, succeeds in [(wheel, sdist, True),
                                                          ('a' * 64, sdist, False),
                                                          (wheel, 'b' * 64, False)]:
            env = {**os.environ, 'EXPECTED_WHEEL_SHA256': expected_wheel,
                   'EXPECTED_SDIST_SHA256': expected_sdist}
            result = subprocess.run(['bash', '-c', script], cwd=self.root, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, succeeds, result.stderr)
        (dist / 'z-extra.whl').write_bytes(b'extra')
        env = {**os.environ, 'EXPECTED_WHEEL_SHA256': wheel, 'EXPECTED_SDIST_SHA256': sdist}
        result = subprocess.run(['bash', '-c', script], cwd=self.root, env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
