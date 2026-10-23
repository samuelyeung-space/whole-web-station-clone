"""Synthetic integration checks; no new market-data or financial replay."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import run_source_race_reason_audit as worker
import test_h_reason_receipt as fixtures


class ReasonWorkerTests(unittest.TestCase):
    def fixture(self):
        rows, counters = fixtures.ReasonReceiptTests().fixture()
        engine = SimpleNamespace(summarize=fixtures.frozen_summary())
        def compare_archive(archive, rows_dir):
            rows_dir.mkdir(parents=True)
            result = {'arms': {}}
            for name in ('baseline', 'candidate'):
                (rows_dir / (name + '.jsonl')).write_text('synthetic-only\n')
                result['arms'][name] = engine.summarize(rows, counters)
            return result
        race = SimpleNamespace(compare_archive=compare_archive)
        return engine, race

    def test_two_arm_comparison_is_unchanged_and_both_receipts_are_complete(self):
        engine, race = self.fixture()
        original = engine.summarize
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            expected = race.compare_archive(root, rows_dir=root / 'old')
            result, receipts = worker.observed_compare(race, engine, root, root / 'observed')
        self.assertEqual(result, expected)
        self.assertIs(engine.summarize, original)
        self.assertEqual([r['arm'] for r in receipts], ['baseline', 'candidate'])
        self.assertTrue(all(r['reason_accounting']['complete'] for r in receipts))
        self.assertTrue(all(not r['economic_admission'] for r in receipts))

    def test_engine_binding_restores_after_comparison_failure(self):
        engine, _ = self.fixture(); original = engine.summarize
        race = SimpleNamespace(compare_archive=lambda *_args, **_kw: (_ for _ in ()).throw(RuntimeError('failed')))
        with self.assertRaisesRegex(RuntimeError, 'failed'):
            worker.observed_compare(race, engine, Path('.'), Path('.'))
        self.assertIs(engine.summarize, original)

    def test_missing_or_extra_summary_is_rejected(self):
        for count in (0, 1, 3):
            engine, _ = self.fixture(); original = engine.summarize
            rows, counters = fixtures.ReasonReceiptTests().fixture()
            def compare(*args, **kwargs):
                for _ in range(count):
                    engine.summarize(rows, counters)
                return {'arms': {}}
            with self.assertRaises(ValueError):
                worker.observed_compare(SimpleNamespace(compare_archive=compare), engine, Path('.'), Path('.'))
            self.assertIs(engine.summarize, original)

    def test_comparison_arm_count_drift_rejected(self):
        engine, race = self.fixture(); original_compare = race.compare_archive
        def changed(*args, **kwargs):
            result = original_compare(*args, **kwargs)
            result['arms']['candidate']['latencies']['500']['signals'] += 1
            return result
        race.compare_archive = changed
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ValueError, 'arm mismatch'):
            worker.observed_compare(race, engine, Path(d), Path(d))

    def test_code_drift_fails_before_engine_import_or_completion(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            worker.write(out / 'registration.json', {
                'observation_code_sha256': {'h_reason_accounting.py': '0' * 64}})
            with self.assertRaisesRegex(ValueError, 'observation code drift'):
                worker.worker(out, out)
            self.assertFalse((out / 'completion.json').exists())

    def test_input_drift_fails_before_engine_import_or_completion(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            worker.write(out / 'registration.json', {
                'observation_code_sha256': {}, 'input_files_sha256': {}})
            with patch.object(worker, 'inputs', return_value={'manifest.json': 'changed'}):
                with self.assertRaisesRegex(ValueError, 'input drift'):
                    worker.worker(out, out)
            self.assertFalse((out / 'completion.json').exists())

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'result.json'
            worker.write(path, {'original': True})
            with self.assertRaises(FileExistsError):
                worker.write(path, {'original': False})
            self.assertEqual(json.loads(path.read_text()), {'original': True})


if __name__ == '__main__':
    unittest.main()
