"""Synthetic engineering checks for unchanged MAR enrichment; not backtests."""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import mean_regime_research as original
import replay_mar_saved_features as adapter


class MarSavedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = 86400000

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        path = self.root / name
        with gzip.open(path, 'wt') as f:
            for row in rows:
                f.write(json.dumps(row, allow_nan=False) + '\n')
        return path

    def tape(self, seconds, future_change=False):
        rows = [{'type': 'dataset', 'recv_ms': self.base, 'dataset_id': 'synthetic',
                 'split': 'development', 'coverage_end_ms': self.base + seconds * 1000}]
        for i in range(seconds + 1):
            t = self.base + i * 1000
            mid = .5 + i * .0001 if not (future_change and i > 150) else .1
            rows.append({'type': 'decision', 'market_id': 'm', 'recv_ms': t,
                         'features': {'mid_up': mid, 'imbalance_up': .2, 'tau_s': 300-i},
                         'feature_available_ms': {'mid_up': t, 'imbalance_up': t, 'tau_s': t}})
        return rows

    def price_rows(self, seconds, future_change=False):
        return [{'receive_ts': (self.base+i*1000)/1000,
                 'trade_ts': (self.base+i*1000-50)/1000,
                 'price': 60000 + .1*i if not (future_change and i > 150) else 100000}
                for i in range(seconds+1)]

    def test_received_future_trade_never_backfilled(self):
        p = self.write('prices.gz', [
            {'receive_ts': 1., 'trade_ts': .9, 'price': 10.},
            {'receive_ts': 3., 'trade_ts': 1.1, 'price': 20.},
        ])
        np.testing.assert_equal(original.build_price_grid(p, [1000, 2000, 3000]), [10,10,20])

    def test_source_regression_ignored_and_stale_quote_missing(self):
        p = self.write('prices.gz', [
            {'receive_ts': 1., 'trade_ts': .9, 'price': 10.},
            {'receive_ts': 2., 'trade_ts': .8, 'price': 999.},
        ])
        values = original.build_price_grid(p, [1000,2000,4000,4001])
        np.testing.assert_equal(values[:3], [10,10,10])
        self.assertTrue(np.isnan(values[3]))

    def test_future_prices_and_poly_movement_do_not_change_prefix(self):
        a = self.write('endpoint_a.gz', self.tape(180))
        b = self.write('endpoint_b.gz', self.tape(180, True))
        pa = self.write('prices_a.gz', self.price_rows(180))
        pb = self.write('prices_b.gz', self.price_rows(180, True))
        ea, _ = original.enrich(a, pa, {})
        eb, _ = original.enrich(b, pb, {})
        prefix_a = [e for e in ea if e['type']=='decision' and e['recv_ms']<=self.base+150000]
        prefix_b = [e for e in eb if e['type']=='decision' and e['recv_ms']<=self.base+150000]
        self.assertEqual(prefix_a, prefix_b)
        self.assertIn('z120', prefix_a[-1]['features'])
        self.assertIn('poly_eff', prefix_a[-1]['features'])

    def test_missing_scores_preserve_all_decisions_and_missing_model(self):
        p = self.write('endpoint.gz', self.tape(180))
        q = self.write('prices.gz', self.price_rows(180))
        events, coverage = original.enrich(p, q, {})
        decs = [e for e in events if e['type']=='decision']
        self.assertEqual(len(decs),181)
        self.assertTrue(all('book_5' not in e['features'] for e in decs))
        self.assertEqual(coverage,{})

    def test_future_mid_does_not_enter_poly_history(self):
        rows = self.tape(20)
        for e in rows[1:]:
            e['feature_available_ms']['mid_up'] = e['recv_ms'] + 1
        p = self.write('endpoint.gz', rows)
        q = self.write('prices.gz', self.price_rows(20))
        events, _ = original.enrich(p, q, {})
        self.assertTrue(all('poly_eff' not in e['features'] for e in events if e['type']=='decision'))

    def test_changed_original_enrichment_hash_rejected_before_reading_inputs(self):
        registration=self.root/'mar.json'
        registration.write_text(json.dumps({'code_sha256':'0'*64}))
        with self.assertRaisesRegex(ValueError, 'original MAR enrichment source changed'):
            adapter.prepare([], 'not-read', 'not-read', 'not-read', registration, self.root/'out')
        self.assertFalse((self.root/'out').exists())


if __name__ == '__main__':
    unittest.main()
