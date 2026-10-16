"""Synthetic tests for the full legacy path, including absent optional Arrow."""
import builtins
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import lake_parquet_adapter as lake
import replay_legacy_pipeline as pipeline
import test_legacy_up_l1_adapter as legacy_tests


class LegacyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = legacy_tests.LegacyL1Tests().make_archive(self.root)
        (self.root / 'legacy_pipeline_test_strategy.py').write_text(
            "def catalog(): return [{'id':'ONE','required_features':['tau_s']}]\n"
            "def signal(c,f): return 'Up'\n")
        self.args = pipeline.parser().parse_args([
            '--input', str(self.archive), '--cache-root', str(self.root / 'cache'),
            '--development-end-before', '1970-01-02T00:00:00Z',
            '--settlement-cutoff-ms', '700000', '--accept-legacy-assumptions',
            '--strategy', 'legacy_pipeline_test_strategy:ONE', '--plugin-dir', str(self.root),
            '--out', str(self.root / 'runs')])

    def tearDown(self):
        sys.modules.pop('legacy_pipeline_test_strategy', None)
        sys.path[:] = [p for p in sys.path if p != str(self.root)]
        self.temp.cleanup()

    def run_pipeline(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return pipeline.execute(self.args)

    def test_complete_pipeline_and_verified_repeat(self):
        first = self.run_pipeline()
        second = self.run_pipeline()
        self.assertFalse(first['legacy_imports'][0]['cache_hit'])
        self.assertTrue(second['legacy_imports'][0]['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(second['new_backtests'], 0)
        self.assertEqual(first['run_path'], second['run_path'])
        self.assertFalse(first['strict_execution_admitted'])
        metrics = json.loads((Path(first['run_path']) / 'metrics.json').read_text())
        self.assertEqual(len(metrics), 3)
        self.assertTrue(all(row['attempts'] == 1 for row in metrics))

    def test_no_implicit_assumptions(self):
        self.args.accept_legacy_assumptions = False
        with self.assertRaisesRegex(ValueError, 'accept-legacy-assumptions'):
            self.run_pipeline()
        self.assertFalse((self.root / 'cache').exists())

    def test_corrupt_cached_tape_not_reused(self):
        first = self.run_pipeline()
        Path(first['legacy_imports'][0]['path']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.run_pipeline()

    def test_future_development_boundary_rejected(self):
        self.args.development_end_before = '1970-01-01T00:05:01Z'
        with self.assertRaisesRegex(ValueError, 'crosses'):
            self.run_pipeline()
        self.assertFalse((self.root / 'runs').exists())

    def test_directory_input_deduplicates_and_empty_fails(self):
        self.assertEqual(pipeline.expand_inputs([self.root, self.archive]), [self.archive])
        empty = self.root / 'empty'; empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'no ZIP'):
            pipeline.expand_inputs([empty])

    def test_missing_file_not_ignored(self):
        with self.assertRaisesRegex(ValueError, 'missing recording'):
            pipeline.expand_inputs([self.root / 'missing.zip'])

    def test_only_parquet_io_needs_arrow(self):
        original = builtins.__import__
        def no_arrow(name, *args, **kwargs):
            if name.startswith('pyarrow'):
                raise ImportError('simulated absent Arrow')
            return original(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=no_arrow):
            self.assertEqual(lake.Series([10], [[1]]).before(11, 100)[2], 'available')
            self.run_pipeline()
            with self.assertRaisesRegex(RuntimeError, 'optional dependency pyarrow'):
                lake.read_columns(['unused.parquet'], ['price'])

    def test_naive_boundary_rejected(self):
        self.args.development_end_before = '1970-01-02T00:00:00'
        with self.assertRaisesRegex(ValueError, 'UTC/offset'):
            self.run_pipeline()


if __name__ == '__main__':
    unittest.main()
