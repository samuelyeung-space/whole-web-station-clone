"""Constraint invalidation and canonical read performance safety acceptance."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import replay_dataset as ds
import replay_engine as engine


class NullConstraintAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        rows = [
            {'type': 'dataset', 'recv_ms': 0, 'dataset_id': 'null-constraint-acceptance',
             'split': 'development', 'provenance': {'source': 'synthetic_acceptance_not_market_backtest'},
             'observed_intervals_ms': [[0, 300000]]},
            {'type': 'market', 'recv_ms': 0, 'market_id': 'm', 'start_ms': 0,
             'end_ms': 300000, 'label_source': 'gamma'},
            {'type': 'constraint', 'recv_ms': 0, 'market_id': 'm', 'min_order_size': 5,
             'up_tick_size': .01, 'down_tick_size': .01, 'constraints_source': 'clob'}]
        for phase, at in ((1, 100), (2, 1100), (3, 2100)):
            if phase == 2:
                rows.append({'type': 'constraint', 'recv_ms': 1000, 'market_id': 'm',
                             'up_tick_size': None, 'down_tick_size': None,
                             'constraints_source': 'clob', 'reason': 'connection_boundary_tick_unknown'})
            if phase == 3:
                rows.append({'type': 'constraint', 'recv_ms': 2000, 'market_id': 'm',
                             'up_tick_size': .01, 'constraints_source': 'clob'})
            rows.extend([
                {'type': 'book', 'recv_ms': at, 'market_id': 'm', 'up_asks': [[.4, 100]],
                 'down_asks': [[.6, 100]], 'healthy': True, 'fee_rate': .07},
                {'type': 'decision', 'recv_ms': at, 'market_id': 'm',
                 'features': {'tau_s': (300000-at)/1000, 'phase': phase},
                 'feature_available_ms': {'tau_s': at, 'phase': at}}])
        rows.append({'type': 'outcome', 'recv_ms': 300000, 'market_id': 'm',
                     'resolved_ms': 300000, 'winner': 'Up', 'source': 'gamma'})
        return rows

    def write(self, rows):
        path = self.root / 'input.jsonl.gz'
        with gzip.open(path, 'wt') as f:
            for row in rows:
                f.write(json.dumps(row) + '\n')
        return path

    @staticmethod
    def execute(rows):
        candidates = [{'id': f'phase-{n}', 'required_features': ['phase'], 'phase': n} for n in (1,2,3)]
        return engine.replay_events(rows, candidates,
            lambda candidate, features: 'Up' if features['phase'] == candidate['phase'] else None,
            engine.ReplayConfig(shares=5, latencies_ms=(500,), require_constraints=True,
                                max_quote_age_ms=1000))

    def test_null_preserved_fails_closed_and_explicit_later_field_restores(self):
        result = ds.ingest(self.write(self.rows()), self.root / 'cache')
        self.assertEqual(result['quality_issues']['null_constraint:up_tick_size'], 1)
        self.assertEqual(result['quality_issues']['null_constraint:down_tick_size'], 1)
        events = list(ds.iter_events(result['cache_path']))
        invalidation = next(e for e in events if e['type'] == 'constraint' and e['recv_ms'] == 1000)
        self.assertIn('up_tick_size', invalidation)
        self.assertIsNone(invalidation['up_tick_size'])
        execution = self.execute(events)
        attempts = {a['candidate_id']: a for a in execution['attempts']}
        self.assertEqual(attempts['phase-1']['filled_shares'], 5)
        self.assertEqual(attempts['phase-2']['filled_shares'], 0)
        self.assertIn('invalid_up_tick_size_metadata', attempts['phase-2']['status'])
        self.assertEqual(attempts['phase-3']['filled_shares'], 5)
        # Only Up was restored; Down stays explicitly invalid.
        from mine500_constraints import check_constraints
        _, error = check_constraints({}, {}, {'recv_ms': 2000, 'min_order_size': 5,
             'up_tick_size': .01, 'down_tick_size': None, 'constraints_source': 'clob'},
             'Down', 2100, 5, .6, require_constraints=True)
        self.assertEqual(error, 'invalid_down_tick_size_metadata')

    def test_fast_cache_read_economic_parity_without_per_row_reserialization(self):
        result = ds.ingest(self.write(self.rows()), self.root / 'cache')
        canonical = Path(result['cache_path']) / 'events.jsonl.gz'
        reference = list(ds._records(canonical))
        with mock.patch.object(ds, '_json', wraps=ds._json) as serialize:
            actual = list(ds.iter_events(result['cache_path']))
        # Manifest and identity are verified; event rows are not serialized again.
        self.assertEqual(serialize.call_count, 2)
        self.assertEqual(actual, reference)
        before, after = self.execute(reference), self.execute(actual)
        for key in ('summary', 'attempts', 'cash_events'):
            self.assertEqual(before[key], after[key])

    def test_corrupted_verified_cache_still_rejected_before_first_event(self):
        result = ds.ingest(self.write(self.rows()), self.root / 'cache')
        canonical = Path(result['cache_path']) / 'events.jsonl.gz'
        data = bytearray(canonical.read_bytes()); data[len(data)//2] ^= 1
        canonical.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'cached tape corrupted'):
            next(ds.iter_events(result['cache_path']))

    def test_nonfinite_inputs_and_future_null_availability_are_not_admitted(self):
        for bad in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(bad=bad):
                rows = self.rows(); rows[3]['up_asks'][0][0] = bad
                with self.assertRaisesRegex(ValueError, 'invalid event'):
                    ds.ingest(self.write(rows), self.root / 'cache')
        rows = self.rows()
        null = next(e for e in rows if e['type'] == 'constraint' and e['recv_ms'] == 1000)
        null['up_tick_size_available_ms'] = 1001
        with self.assertRaisesRegex(ValueError, 'constraint future availability'):
            ds.ingest(self.write(rows), self.root / 'cache')


if __name__ == '__main__':
    unittest.main()
