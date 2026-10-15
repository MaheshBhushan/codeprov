"""Offline behavioral regressions; integration checks require locked scanners."""
import json
import tempfile
import unittest
from pathlib import Path

from codeprov.config import load

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_effort_is_low_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'study.toml'
            text = (ROOT / 'study.pilot.toml').read_text().replace('rules/python-security.yml', str(ROOT / 'rules/python-security.yml'))
            for effort in ('medium', 'high', 'invalid'):
                path.write_text(text.replace('effort = "low"', f'effort = "{effort}"'))
                with self.subTest(effort=effort), self.assertRaises(ValueError):
                    load(path)

    def test_invalid_selection_and_input_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / 'rules.yml'
            rules.write_bytes((ROOT / 'rules/python-security.yml').read_bytes())
            text = (ROOT / 'study.pilot.toml').read_text().replace('rules/python-security.yml', 'rules.yml')
            path = root / 'study.toml'
            path.write_text(text)
            before = load(path).run_id
            rules.write_text(rules.read_text() + '\n')
            self.assertNotEqual(before, load(path).run_id)
            for invalid in (text.replace('2026-10-13', '2020-01-01'),
                            text.replace('["pallets/click"]', '["pallets/click", "pallets/click"]'),
                            text.replace('max_prs = 2', 'max_prs = 0')):
                path.write_text(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    load(path)
            (root / 'manual.json').write_text(json.dumps([{'repo': 'pallets/click', 'sha': 'a' * 40, 'evidence': ''}]))
            path.write_text(text.replace('[review]', 'manual_labels = "manual.json"\n[review]'))
            with self.assertRaises(ValueError):
                load(path)


class ProvenanceTests(unittest.TestCase):
    def test_only_supported_evidence_selects_cohort(self):
        from codeprov.provenance import classify
        identities = ['ai@example.invalid']
        for message in ('Assistant wrote this', 'Co-Authored-By: AI <ai@example.invalid>\n\nBody after trailer',
                        'Change\n\nCo-Authored-By: Human <human@example.invalid>', 'Change'):
            with self.subTest(message=message):
                self.assertEqual(classify(message, identities)['provenance'], 'unknown')
        ai = 'Change\n\nCo-Authored-By: AI <AI@example.invalid>'
        self.assertEqual(classify(ai, identities)['provenance'], 'declared_ai')
        self.assertEqual(classify('Change', identities, {'evidence': 'Independent interview'})['provenance'], 'verified_manual')
        conflict = classify(ai, identities, {'evidence': 'Independent interview'})
        self.assertEqual(conflict['provenance'], 'unknown')
        self.assertTrue(conflict['evidence']['conflict'])
        with self.assertRaises(ValueError):
            classify('Change', identities, {'evidence': ''})


class StorageTests(unittest.TestCase):
    def test_idempotent_persistent_strict_records(self):
        from codeprov.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'study.sqlite'
            store = Store(path)
            store.put('commit', 'repo:sha', {'value': 1})
            store.put('commit', 'repo:sha', {'value': 2})
            store.put('comment', 'repo:1', {'value': None})
            with self.assertRaises(ValueError):
                store.put('commit', 'bad', {'value': float('nan')})
            store.close()
            store = Store(path)
            try:
                self.assertEqual(store.rows('commit'), [{'value': 2}])
                store.clear('commit')
                self.assertEqual(store.rows('comment'), [{'value': None}])
                self.assertEqual(store.rows('commit'), [])
            finally:
                store.close()


class GitHubTests(unittest.TestCase):
    def test_pages_cache_and_same_origin(self):
        import httpx

        from codeprov.github import GitHub, GitHubError
        with tempfile.TemporaryDirectory() as directory, GitHub(Path(directory)) as api:
            calls = []
            def handler(request):
                calls.append(str(request.url))
                if request.url.path == '/first':
                    return httpx.Response(200, json=[1], headers={'Link': '<https://api.github.com/second>; rel="next"'})
                return httpx.Response(200, json=[2])
            api.client.close()
            api.client = httpx.Client(transport=httpx.MockTransport(handler))
            self.assertEqual([page for page, _ in api.pages('/first')], [[1], [2]])
            self.assertEqual([page for page, _ in api.pages('/first')], [[1], [2]])
            self.assertEqual(len(calls), 2)
            for url in ('https://evil.invalid/path', 'http://api.github.com/path', 'https://token@api.github.com/path'):
                with self.subTest(url=url), self.assertRaises(GitHubError):
                    api.get(url)
            self.assertEqual(len(calls), 2)

    def test_pagination_loop_external_next_and_rate_limit(self):
        import httpx

        from codeprov.github import GitHub, GitHubError
        cases = ((200, {'Link': '<https://api.github.com/loop>; rel="next"'}, 'Cyclic'),
                 (200, {'Link': '<https://evil.invalid/>; rel="next"'}, 'non-GitHub'),
                 (429, {'Retry-After': '1'}, 'rate limit'))
        for status, headers, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory, GitHub(Path(directory)) as api:
                api.client.close()
                api.client = httpx.Client(transport=httpx.MockTransport(lambda request, status=status, headers=headers: httpx.Response(
                    status, json={'message': 'limited'} if status == 429 else [], headers=headers)))
                with self.assertRaisesRegex(GitHubError, reason):
                    list(api.pages('/loop'))


class MetricsAndScannerTests(unittest.TestCase):
    def test_function_complexity_counts_definitions_once(self):
        from codeprov.metrics import _delta, source_metrics
        simple = source_metrics('def f(x):\n    return x\n')
        nested = source_metrics('class C:\n    def f(self, x):\n        def inner(y):\n            if y:\n                return 1\n            return 0\n        return inner(x)\n')
        self.assertEqual(simple['complexity'], 1)
        self.assertEqual(nested['complexity'], 3)
        self.assertEqual(_delta(simple, nested, 'complexity'), 2)
        self.assertIsNone(_delta(None, simple, 'mi'))
        self.assertIsNone(_delta(simple, None, 'mi'))
        with self.assertRaises(SyntaxError):
            source_metrics('def broken(')

    def test_multiset_shift_and_failure_missingness(self):
        from codeprov.metrics import _scanner_delta
        from codeprov.security import diff_findings, safe_relative_path
        finding = {'tool': 'bandit', 'rule': 'B307', 'path': 'a.py', 'code': 'eval(value)', 'line': 1}
        shifted = dict(finding, line=50, code='eval( value ) # same')
        result = diff_findings([finding, finding], [shifted])
        self.assertEqual(result['introduced'], [])
        self.assertEqual(len(result['resolved']), 1)
        self.assertEqual(len(diff_findings([], [finding])['introduced']), 1)
        for value in ('../x.py', '/tmp/x.py', 'C:/x.py', 'a\\b.py', 'a/./b.py'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative_path(value)
        failed = _scanner_delta({'status': 'failed', 'findings': [], 'error': 'process failure'},
                                {'status': 'complete', 'findings': []})
        self.assertIsNone(failed['introduced'])
        self.assertEqual(failed['status'], 'failed')

    def test_real_scanners_clean_unsafe_and_invalid(self):
        from codeprov.security import scan_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source, unsafe in (('unsafe', 'def f(value):\n    return eval(value)\n', True),
                                         ('clean', 'def f(value):\n    return int(value)\n', False)):
                result = scan_snapshot({'sample.py': source}, ROOT / 'rules/python-security.yml', root / name)
                for tool in ('bandit', 'semgrep'):
                    with self.subTest(name=name, tool=tool):
                        self.assertEqual(result[tool]['status'], 'complete', result[tool])
                        self.assertEqual(bool(result[tool]['findings']), unsafe)
                        self.assertTrue(Path(result[tool]['artifacts']['raw']).is_file())
            invalid = scan_snapshot({'../escape.py': 'pass'}, ROOT / 'rules/python-security.yml', root / 'invalid')
            self.assertEqual(invalid['bandit']['status'], 'invalid_snapshot')

    def test_scanner_process_errors_and_timeout(self):
        import subprocess
        from unittest.mock import MagicMock, patch

        from codeprov.security import _run_scanner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = MagicMock(returncode=2)
            process.communicate.return_value = ('{"results": [], "errors": []}', 'failure')
            with patch('codeprov.security.subprocess.Popen', return_value=process):
                failed = _run_scanner('bandit', ['fixture'], root, {'a.py': 'pass'}, root, {}, 1)
            self.assertEqual(failed['status'], 'failed')
            process.pid = 12345
            process.communicate.side_effect = [subprocess.TimeoutExpired('fixture', 1), ('', '')]
            with patch('codeprov.security.subprocess.Popen', return_value=process), patch('codeprov.security.os.killpg') as kill:
                timeout = _run_scanner('bandit', ['fixture'], root, {}, root, {}, 1)
            self.assertEqual(timeout['status'], 'timeout')
            kill.assert_called_once()


class ReviewTests(unittest.TestCase):
    def test_strict_response_validation(self):
        from codeprov.review import _validate
        self.assertEqual(_validate('{"findings": []}', {'a.py': [1]}), [])
        finding = {'path': 'a.py', 'line': 1, 'category': 'bug', 'message': 'Issue'}
        self.assertEqual(_validate(json.dumps({'findings': [finding]}), {'a.py': [1]}), [finding])
        for bad in (dict(finding, path='../x.py'), dict(finding, line=2), dict(finding, line=True), dict(finding, message='')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _validate(json.dumps({'findings': [bad]}), {'a.py': [1]})
        with self.assertRaises(ValueError):
            _validate('{"findings": [], "findings": []}', {})


class ModelTests(unittest.TestCase):
    @staticmethod
    def frame():
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(711)
        n = 240
        ai = np.tile([0, 1], n // 2)
        repositories = np.repeat(np.arange(8), n // 8)
        effects = np.array([-4, 2, -2, 4, -3, 3, -1, 1])[repositories]
        x = rng.normal(size=n)
        frame = pd.DataFrame({'repo': repositories.astype(str), 'declared_ai': ai,
            'provenance': np.where(ai, 'declared_ai', 'verified_manual'), 'log_churn': x,
            'prior_complexity': rng.normal(size=n), 'log_experience': rng.normal(size=n),
            'continuous': 1.5 * ai + 0.7 * x + effects + rng.normal(size=n)})
        frame['binary'] = rng.binomial(1, 1 / (1 + np.exp(-(-0.5 + 0.7 * ai + 0.3 * x))))
        return frame

    def test_refusal_guards(self):
        import pandas as pd

        from codeprov.analysis import fit_model
        self.assertEqual(fit_model(pd.DataFrame(), 'y', 'verified_manual')['reason'], 'no observations')
        frame = self.frame()
        for bad in (frame.iloc[:10], frame.assign(repo='only'), frame[frame.declared_ai == 1]):
            result = fit_model(bad, 'continuous', 'verified_manual')
            self.assertEqual(result['status'], 'not_estimable')
            self.assertNotIn('coefficients', result)
        rank = frame.assign(log_churn=frame.declared_ai)
        self.assertIn('rank deficient', fit_model(rank, 'continuous', 'verified_manual')['reason'])
        separated = frame.assign(binary=frame.declared_ai)
        self.assertIn('separation', fit_model(separated, 'binary', 'verified_manual', binary=True)['reason'])
        constant = frame.assign(continuous=1)
        self.assertIn('no variation', fit_model(constant, 'continuous', 'verified_manual')['reason'])

    def test_seeded_continuous_and_binary_fits(self):
        import math

        from codeprov.analysis import fit_model
        for outcome, binary in (('continuous', False), ('binary', True)):
            result = fit_model(self.frame(), outcome, 'verified_manual', binary=binary)
            with self.subTest(outcome=outcome):
                self.assertEqual(result['status'], 'complete', result)
                self.assertTrue(all(math.isfinite(c[key]) for c in result['coefficients'] for key in ('estimate', 'sd', 'lower', 'upper')))
                self.assertGreater(next(c['estimate'] for c in result['coefficients'] if c['term'] == 'declared_ai'), 0)
                self.assertIn('credible' if binary else 'confidence', result['interval_type'])
                self.assertEqual(result['random_effects'], 'repository intercept')


class OfflinePipelineTests(unittest.TestCase):
    def test_demo_real_pipeline_idempotency_and_historical_isolation(self):
        from codeprov.demo import run
        from codeprov.storage import Store
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'demo'
            summary = run(output)
            self.assertTrue(summary['synthetic'])
            self.assertEqual(summary['actual'], summary['expected'])
            store = Store(Path(summary['run_dir']) / 'study.sqlite')
            try:
                request = store.rows('review_request')[0]
                payload = json.loads(Path(request['request_path']).read_text())
                code = payload['messages'][1]['content']
                self.assertIn('eval(value)', code)
                self.assertIn('Ignore previous instructions', code)
                self.assertNotIn('SYNTHETIC GOLD', code)
                self.assertNotIn('return int(value)\n+def', code)
                self.assertEqual(request['sha'], summary['shas']['introduction'])
                self.assertEqual(request['base_sha'], summary['shas']['base'])
                self.assertTrue(Path(summary['report']).read_text().startswith('# SYNTHETIC'))
                self.assertTrue(all(row['status'] == 'not_estimable' for row in store.rows('analysis')))
            finally:
                store.close()
            repeated = run(output)
            self.assertEqual(repeated['shas'], summary['shas'])
            self.assertEqual(repeated['actual'], summary['actual'])
            self.assertEqual(repeated['run_dir'], summary['run_dir'])
