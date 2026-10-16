"""Sharding equivalence and resume integrity on synthetic acceptance data."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import replay_catalog_batch as batch
import replay_lab as lab


class CatalogBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'shard_fixture_plugin.py').write_text(
            "def catalog():\n"
            " return [{'id':'BUY-UP','required_features':['x']},"
            "{'id':'BUY-DOWN','required_features':['x']},"
            "{'id':'MISSING','required_features':['absent']},"
            "{'id':'NO-SIGNAL','required_features':['x']}]\n"
            "def signal(c,f):\n"
            " return {'BUY-UP':'Up','BUY-DOWN':'Down'}.get(c['id'])\n")
        tape = self.root / 'data.jsonl'
        rows = [
            {'type': 'dataset', 'recv_ms': 0, 'dataset_id': 'synthetic-batch-acceptance',
             'split': 'development', 'provenance': {'source': 'synthetic_acceptance_not_market_backtest'},
             'coverage_start_ms': 0, 'coverage_end_ms': 300000},
            {'type': 'market', 'recv_ms': 0, 'market_id': 'm', 'start_ms': 0,
             'end_ms': 300000, 'label_source': 'gamma'},
            {'type': 'constraint', 'recv_ms': 0, 'market_id': 'm', 'min_order_size': 5,
             'up_tick_size': .01, 'down_tick_size': .01, 'constraints_source': 'gamma'},
            {'type': 'book', 'recv_ms': 100, 'market_id': 'm', 'up_asks': [[.4, 100]],
             'down_asks': [[.6, 100]], 'healthy': True, 'fee_rate': .07},
            {'type': 'decision', 'recv_ms': 100, 'market_id': 'm',
             'features': {'tau_s': 299.9, 'x': 1}, 'feature_available_ms': {'tau_s': 100, 'x': 100}},
            {'type': 'outcome', 'recv_ms': 300000, 'market_id': 'm', 'resolved_ms': 300000,
             'winner': 'Up', 'source': 'gamma'}]
        tape.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        cache = lab.import_inputs([tape], self.root / 'cache', 'normalized')['cache_path']
        self.args = batch.parser().parse_args([
            '--cache', cache, '--out', str(self.root / 'runs'), '--batch-dir', str(self.root / 'batch'),
            '--strategy', 'shard_fixture_plugin:*', '--plugin-dir', str(self.root),
            '--batch-size', '2', '--shares', '5,10', '--latencies-ms', '500,1000',
            '--stresses', 'base,half_depth', '--max-quote-age-ms', '1500'])

    def tearDown(self):
        sys.modules.pop('shard_fixture_plugin', None)
        sys.path[:] = [item for item in sys.path if item != str(self.root)]
        self.tmp.cleanup()

    def run_batch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return batch.run_batch(self.args)

    @staticmethod
    def metrics(path):
        return json.loads((Path(path) / 'metrics.json').read_text())

    def test_shards_equal_unsharded_independent_cash_and_full_preregistration(self):
        original = lab.run_cached
        def checked(args, *a, **k):
            ledgers = list((self.root / 'batch').glob('*/registration.json'))
            self.assertEqual(len(ledgers), 1)
            registration = json.loads(ledgers[0].read_text())
            self.assertEqual(registration['registered_candidate_count'], 4)
            self.assertEqual(registration['candidate_ids_in_catalog_order'],
                             ['BUY-UP', 'BUY-DOWN', 'MISSING', 'NO-SIGNAL'])
            return original(args, *a, **k)
        with mock.patch.object(lab, 'run_cached', side_effect=checked):
            split = self.run_batch()
        whole_args = copy.deepcopy(self.args)
        whole_args.out = str(self.root / 'unsharded')
        with contextlib.redirect_stdout(io.StringIO()):
            whole = original(whole_args)
        key = lambda row: (row['candidate_id'], row['scenario'], row['latency_ms'])
        self.assertEqual(sorted(self.metrics(split['batch_path']), key=key),
                         sorted(self.metrics(whole['run_path']), key=key))
        summary = json.loads((Path(split['batch_path']) / 'summary.json').read_text())
        self.assertEqual(summary['candidate_class_counts'],
                         {'evaluated_all_scenarios': 3, 'blocked_all_scenarios': 1})
        self.assertEqual(summary['multiple_testing_denominator_floor_this_catalog'], 4)
        self.assertIsNone(summary['net_pnl_sum_across_candidates'])
        values = self.metrics(split['batch_path'])
        self.assertTrue(all(row['initial_cash_usd'] == 1000 for row in values))
        base_up = next(row for row in values if row['candidate_id'] == 'BUY-UP'
                       and row['scenario'] == 'shares5_base_release0' and row['latency_ms'] == 500)
        self.assertAlmostEqual(base_up['net_pnl_usd'], 2.916)
        self.assertEqual(split['new_backtests'], 32)

    def test_parallel_economics_equal_serial_and_resume_without_new_execution(self):
        serial = self.run_batch()
        serial_shards = json.loads((Path(serial['batch_path'])/'shards.json').read_text())
        self.args.workers = 2
        self.args.out = str(self.root/'parallel_runs')
        parallel = self.run_batch()
        parallel_shards = json.loads((Path(parallel['batch_path'])/'shards.json').read_text())
        self.assertEqual(self.metrics(serial['batch_path']), self.metrics(parallel['batch_path']))
        self.assertEqual([x['index'] for x in parallel_shards], [0,1])
        for left,right in zip(serial_shards,parallel_shards):
            self.assertEqual(left['candidate_ids'],right['candidate_ids'])
            self.assertEqual(left['run_key'],right['run_key'])
            lroot,rroot=Path(left['run_path']),Path(right['run_path'])
            for p in lroot.glob('*/cash_ledger.csv'):
                self.assertEqual(p.read_bytes(),(rroot/p.relative_to(lroot)).read_bytes())
            import gzip
            for p in lroot.glob('*/attempts.jsonl.gz'):
                with gzip.open(p,'rb') as l, gzip.open(rroot/p.relative_to(lroot),'rb') as r:
                    self.assertEqual(l.read(),r.read())
        again=self.run_batch()
        self.assertEqual(again['new_backtests'],0)
        self.assertTrue(again['cache_hit'])

    def test_worker_bounds_and_parallel_shared_are_rejected(self):
        for workers in [0,9]:
            self.args.workers=workers
            with self.assertRaisesRegex(ValueError,'workers'):
                self.run_batch()
        self.args.workers=2; self.args.mode='shared'
        with self.assertRaisesRegex(ValueError,'isolated mode only'):
            self.run_batch()

    def test_resume_calls_full_cache_checks_without_new_execution(self):
        first = self.run_batch()
        before = (Path(first['batch_path']) / 'registration.json').read_bytes()
        import replay_runner
        with mock.patch.object(replay_runner, 'execute', side_effect=AssertionError('new execution on resume')):
            second = self.run_batch()
        self.assertTrue(second['cache_hit'])
        self.assertEqual(second['new_backtests'], 0)
        self.assertEqual(first['batch_path'], second['batch_path'])
        self.assertEqual(before, (Path(second['batch_path']) / 'registration.json').read_bytes())

    def test_shared_rejected_before_registration_or_execution(self):
        self.args.mode = 'shared'
        with mock.patch.object(lab, 'run_cached', side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(ValueError, 'isolated mode only'):
                self.run_batch()
        self.assertFalse((self.root / 'batch').exists())

    def test_corrupt_shard_ledger_is_rejected_on_resume(self):
        first = self.run_batch()
        shards = json.loads((Path(first['batch_path']) / 'shards.json').read_text())
        ledger = next(Path(shards[0]['run_path']).glob('*/attempts.jsonl.gz'))
        ledger.write_bytes(ledger.read_bytes() + b'corruption')
        with self.assertRaisesRegex(ValueError, 'cached run output corrupted'):
            self.run_batch()

    def test_non_catalog_order_and_zero_size_rejected(self):
        self.args.priority = ['MISSING', 'BUY-UP', 'BUY-DOWN', 'NO-SIGNAL']
        with self.assertRaisesRegex(ValueError, 'default catalog priority'):
            self.run_batch()
        self.args.priority = None
        self.args.batch_size = 0
        with self.assertRaisesRegex(ValueError, 'batch-size must be positive'):
            self.run_batch()


if __name__ == '__main__':
    unittest.main()
