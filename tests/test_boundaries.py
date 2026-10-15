"""Local history, annotation completeness, and missing-data boundaries."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from codeprov.cli import initialize
from codeprov.config import Study
from codeprov.demo import REPO, history
from codeprov.mining import _commit

ROOT = Path(__file__).resolve().parents[1]


class HistoryBoundaries(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.shas = history(self.root / 'history')
        self.labels = self.root / 'fixes.json'
        self.labels.write_text(json.dumps([{'repo': REPO, 'sha': self.shas['fix'], 'evidence': 'Synthetic fix'}]))
        self.study = Study((REPO,), '2020-01-01', '2020-01-05', '2020-02-10', 30, 1,
                           ('ai@example.invalid',), ROOT / 'rules/python-security.yml', None, self.labels,
                           {'endpoint': 'http://localhost:1/', 'model': 'fixture', 'effort': 'low',
                            'max_chars': 60000, 'development_repositories': []})
        self.store, self.run_dir = initialize(self.study, self.root / 'runs')
        self.addCleanup(self.store.close)
        self.store.put('repo', REPO, {'repo': REPO, 'path': str(self.root / 'history'), 'mainline_sha': self.shas['fix']})
        for name in ('base', 'introduction', 'manual', 'unknown'):
            row = _commit(self.root / 'history', REPO, self.shas[name], self.study.ai_identities, None)
            self.store.put('commit', REPO + ':' + row['sha'], row)
        self.store.put('pr', REPO + ':1', {'repo': REPO, 'number': 1, 'base_sha': self.shas['base'],
            'head_sha': self.shas['fix'], 'merge_sha': self.shas['fix'], 'commits': list(self.shas.values())})
        self.store.put('comment', REPO + ':1', {'repo': REPO, 'pr_number': 1, 'id': 1,
            'original_commit_id': self.shas['introduction'], 'path': 'example.py', 'body': 'GOLD LABEL',
            'user': {'type': 'User'}})

    def test_szz_known_introduction_missing_labels_and_censoring(self):
        from codeprov.szz import attribute
        result = attribute(self.study, self.store, self.run_dir)
        self.assertEqual(result['attributed_commits'], 1)
        self.assertEqual(self.store.get('szz_outcome', REPO + ':' + self.shas['introduction'])['candidate_bug'], 1)
        self.assertEqual(self.store.get('szz_outcome', REPO + ':' + self.shas['base'])['reason'], 'root_commit_excluded')
        missing = attribute(replace(self.study, bug_fixes=None), self.store, self.run_dir)
        self.assertEqual(missing['status'], 'pending')
        self.assertTrue(all(row['candidate_bug'] is None for row in self.store.rows('szz_outcome')))
        attribute(replace(self.study, observed_until='2020-01-10'), self.store, self.run_dir)
        row = self.store.get('szz_outcome', REPO + ':' + self.shas['introduction'])
        self.assertEqual(row['reason'], 'right_censored')
        self.assertIsNone(row['candidate_bug'])

    def test_root_fix_exclusion(self):
        from codeprov.szz import attribute
        self.labels.write_text(json.dumps([{'repo': REPO, 'sha': self.shas['base'], 'evidence': 'Synthetic invalid root fix'}]))
        attribute(self.study, self.store, self.run_dir)
        self.assertEqual(self.store.rows('szz')[0]['reason'], 'root_fix_excluded')
        self.assertTrue(all(row['candidate_bug'] is None for row in self.store.rows('szz_outcome')))

    def test_pending_evidence_stays_visible_in_report(self):
        from codeprov.report import report
        from codeprov.szz import attribute
        result = attribute(replace(self.study, bug_fixes=None), self.store, self.run_dir)
        self.store.stage('szz', result['status'], result)
        paths = report(self.study, self.store, self.run_dir)
        text = Path(paths['report']).read_text()
        self.assertIn('| szz | pending |', text)
        evidence = json.loads(Path(paths['evidence']).read_text())
        self.assertTrue(all(row['candidate_bug'] is None for row in evidence['szz_outcome']))

    def test_merge_commit_and_fix_exclusion(self):
        import subprocess

        from codeprov.mining import _git, _git_environment
        from codeprov.szz import attribute
        root = self.root / 'history'
        env = _git_environment()
        env.update(GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='a@example.invalid',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='a@example.invalid',
                   GIT_AUTHOR_DATE='2020-01-06T12:00:00+00:00', GIT_COMMITTER_DATE='2020-01-06T12:00:00+00:00')
        tree = _git(root, 'rev-parse', self.shas['fix'] + '^{tree}').stdout.strip()
        merge = subprocess.run(['git', '-C', str(root), 'commit-tree', tree, '-p', self.shas['fix'],
            '-p', self.shas['base'], '-m', 'Synthetic merge'], env=env, check=True,
            capture_output=True, text=True, timeout=30).stdout.strip()
        self.store.put('repo', REPO, {'repo': REPO, 'path': str(root), 'mainline_sha': merge})
        row = _commit(root, REPO, merge, self.study.ai_identities, None)
        self.assertEqual(row['delta_status'], 'excluded_merge')
        self.store.put('commit', REPO + ':' + merge, row)
        self.labels.write_text(json.dumps([{'repo': REPO, 'sha': merge, 'evidence': 'Synthetic merge fix'}]))
        attribute(self.study, self.store, self.run_dir)
        self.assertEqual(self.store.get('szz_outcome', REPO + ':' + merge)['reason'], 'merge_commit_excluded')
        self.assertEqual(self.store.rows('szz')[0]['reason'], 'merge_fix_excluded')

    def test_oversized_historical_request_is_skipped(self):
        from codeprov.review import review
        result = review(replace(self.study, review=dict(self.study.review, max_chars=1)),
                        self.store, self.run_dir, export_only=True)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('entire request skipped', self.store.rows('review_request')[0]['reason'])
        self.assertEqual(self.store.rows('review_finding'), [])

    def test_http_failure_is_not_a_review(self):
        import httpx

        from codeprov.review import review
        original = httpx.Client
        def client(**kwargs):
            return original(transport=httpx.MockTransport(lambda request: httpx.Response(500, json={'error': 'fixture'})), **kwargs)
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic'}), patch('codeprov.review.httpx.Client', client):
            result = review(self.study, self.store, self.run_dir)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['reviewed'], 0)
        self.assertEqual(self.store.rows('review_finding'), [])
        self.assertEqual(self.store.rows('review_response')[0]['http_status'], 500)

    def test_empty_http_response_is_completed_review(self):
        import httpx

        from codeprov.review import review
        original = httpx.Client
        def client(**kwargs):
            body = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"findings": []}'}}]}
            return original(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)), **kwargs)
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic'}), patch('codeprov.review.httpx.Client', client):
            result = review(self.study, self.store, self.run_dir)
        self.assertEqual(result['reviewed'], 1)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.store.rows('review_request')[0]['finding_count'], 0)
        self.assertEqual(self.store.rows('review_finding'), [])


class AdjudicationBoundaries(unittest.TestCase):
    def setUp(self):
        from codeprov.storage import Store
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'study.sqlite')
        self.addCleanup(self.store.close)
        self.study = type('StudyFixture', (), {'review': {'development_repositories': []}})()
        self.sid = REPO + ':1:' + 'a' * 40
        self.store.put('review_request', self.sid, {'id': self.sid, 'repo': REPO, 'pr_number': 1,
            'sha': 'a' * 40, 'split': 'test', 'status': 'reviewed', 'request_path': 'synthetic'})
        for comment_id in (1, 2, 3):
            self.store.put('comment', str(comment_id), {'id': comment_id, 'repo': REPO, 'pr_number': 1,
                'original_commit_id': 'a' * 40, 'path': 'a.py', 'user': {'type': 'User'}, 'body': 'Synthetic'})
        for finding_id in ('f1', 'f2', 'f3', 'f4'):
            self.store.put('review_finding', finding_id, {'id': finding_id, 'snapshot_id': self.sid})
        self.labels = {'version': 1, 'snapshots': [{'snapshot_id': self.sid, 'split': 'test',
            'comments': [{'comment_id': i, 'disposition': 'issue', 'canonical_comment_id': 1 if i < 3 else 3,
                          'rationale': 'Synthetic grouping'} for i in (1, 2, 3)],
            'finding_groups': [{'canonical_finding_id': canonical, 'finding_ids': members,
                'genuine': genuine, 'rationale': 'Synthetic validity'} for canonical, members, genuine in
                (('f1', ['f1', 'f2'], True), ('f3', ['f3'], True), ('f4', ['f4'], False))],
            'matches': [{'finding_id': 'f1', 'comment_id': 1, 'rationale': 'Synthetic match'}]}]}
        self.path = self.root / 'annotations.json'

    def evaluate(self):
        from codeprov.evaluation import evaluate
        self.path.write_text(json.dumps(self.labels))
        return evaluate(self.study, self.store, self.root, self.path)

    def test_exact_counts_deduplication_and_genuine_unmatched(self):
        total = self.evaluate()['total']
        self.assertEqual((total['tp'], total['fp'], total['fn']), (1, 2, 1))
        self.assertEqual(total['precision'], 1 / 3)
        self.assertEqual(total['recall'], 1 / 2)
        self.assertEqual(total['adjudicated_precision'], 2 / 3)
        self.assertEqual(total['genuine_unmatched'], 1)

    def test_invalid_coverage_invalidates_old_scores(self):
        self.evaluate()
        self.assertEqual(len(self.store.rows('evaluation')), 1)
        self.labels['snapshots'][0]['finding_groups'].pop()
        with self.assertRaisesRegex(ValueError, 'every finding'):
            self.evaluate()
        self.assertEqual(self.store.rows('evaluation'), [])
        self.assertEqual(self.store.rows('analysis'), [])
        self.assertEqual(next(stage['status'] for stage in self.store.stages() if stage['name'] == 'evaluate'), 'pending')

    def test_duplicate_matches_and_pending_labels_rejected(self):
        snapshot = self.labels['snapshots'][0]
        snapshot['matches'].append(snapshot['matches'][0].copy())
        with self.assertRaisesRegex(ValueError, 'one-to-one'):
            self.evaluate()
        snapshot['matches'].pop()
        snapshot['finding_groups'][0]['genuine'] = None
        with self.assertRaisesRegex(ValueError, 'boolean'):
            self.evaluate()

    def test_empty_denominators_remain_null(self):
        from codeprov.evaluation import _empty, _rates
        rates = _rates(_empty())
        self.assertIsNone(rates['precision'])
        self.assertIsNone(rates['recall'])
        self.assertIsNone(rates['adjudicated_precision'])


class MeasurementBoundaries(unittest.TestCase):
    def test_add_delete_parse_and_missing_source(self):
        from codeprov.metrics import measure
        from codeprov.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / 'study.sqlite')
            self.addCleanup(store.close)
            files = [
                {'path': 'add.py', 'old_path': None, 'new_path': 'add.py', 'before': None, 'after': 'x = 1\n'},
                {'path': 'delete.py', 'old_path': 'delete.py', 'new_path': None, 'before': 'x = 1\n', 'after': None},
                {'path': 'parse.py', 'old_path': 'parse.py', 'new_path': 'parse.py', 'before': 'x = 1\n', 'after': 'def broken('},
                {'path': 'missing.py', 'old_path': 'missing.py', 'new_path': 'missing.py', 'before': None, 'after': 'x = 1\n'},
            ]
            store.put('commit', 'fixture', {'repo': REPO, 'sha': 'a' * 40, 'parents': ['b' * 40], 'files': files})
            def scan(*args):
                return {tool: {'status': 'complete', 'findings': [], 'artifacts': {}} for tool in ('bandit', 'semgrep')}
            study = type('StudyFixture', (), {'semgrep_rules': ROOT / 'rules/python-security.yml'})()
            with patch('codeprov.metrics.scan_snapshot', scan):
                result = measure(study, store, root)
            self.assertEqual(result['partial'], 2)
            rows = {row['path']: row for row in store.rows('measurement')}
            for name in ('add.py', 'delete.py', 'parse.py', 'missing.py'):
                self.assertIsNone(rows[name]['mi_delta'])
            self.assertEqual(rows['missing.py']['bandit']['status'], 'invalid_snapshot')
            self.assertIsNone(rows['missing.py']['bandit']['introduced'])
            self.assertEqual(rows['add.py']['bandit']['before']['status'], 'not_applicable')
            self.assertEqual(rows['delete.py']['bandit']['after']['status'], 'not_applicable')


class NonfiniteModelBoundary(unittest.TestCase):
    def test_nonfinite_controls_refuse_without_crashing(self):
        from test_pipeline import ModelTests

        from codeprov.analysis import fit_model
        frame = ModelTests.frame()
        frame.loc[0, 'log_churn'] = float('inf')
        result = fit_model(frame, 'continuous', 'verified_manual')
        self.assertEqual(result['status'], 'not_estimable')
        self.assertIn('nonfinite', result['reason'])
