"""Engineering counterexamples only; not market backtests or OOS evidence."""
import gzip
import io
import json
import math
from pathlib import Path
import tarfile
import tempfile
import unittest

import historical100_runner as R


class RunnerTests(unittest.TestCase):
    def test_roundoff_does_not_relax_execution_identity(self):
        original = [dict(signal_id='s', score=.06722820314007796, cut=.05,
                         asklimit=.5, decision_ms=1000., side='up')]
        rebuilt = [dict(original[0], score=math.nextafter(original[0]['score'], math.inf))]
        audit = R.compare_continuation_signals(original, rebuilt)
        self.assertEqual(audit['score_roundoff_rows'], 1)
        self.assertTrue(audit['execution_uses_original_signals'])
        for field, value in [('asklimit', .5000000000000001), ('decision_ms', 1000.0000000001),
                             ('side', 'down'), ('signal_id', 'other'), ('cut', .05000000000000001)]:
            changed = [dict(rebuilt[0], **{field: value})]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'economic fields'):
                R.compare_continuation_signals(original, changed)
        self.assertNotEqual(original[0]['score'], rebuilt[0]['score'])
        with self.assertRaisesRegex(ValueError, 'row count'):
            R.compare_continuation_signals(original, [])

    def test_roundoff_rejects_threshold_crossing_nonfinite_and_larger_drift(self):
        a = dict(signal_id='s', score=.05, cut=.05)
        for score, error in [(math.nextafter(.05, 0.), 'threshold side'),
                             (.050001, 'exceeds 8 ULP'), (float('nan'), 'invalid score'),
                             (float('inf'), 'invalid score'), (True, 'invalid score')]:
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, error):
                R.compare_continuation_signals([a], [dict(a, score=score)])
        with self.assertRaisesRegex(ValueError, 'economic fields'):
            R.compare_continuation_signals([a, dict(a, signal_id='t')],
                                           [dict(a, signal_id='t'), a])

    def test_rule_subset_does_not_change_latency_or_capital(self):
        args = R.parse_args(['--rules', 'R3', '--workdir', '/tmp/a', '--out', '/tmp/b'])
        self.assertEqual((args.rules,args.latencies,args.shares,args.initial_cash,args.entry_fill_policy),
                         (['R3'],[500,600,1000],100,1000,'full100_or_zero'))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            R.parse_args(['--rules','R3','R3','--workdir','/tmp/a','--out','/tmp/b'])
        with self.assertRaisesRegex(ValueError, 'fixes'):
            R.parse_args(['--shares','20','--workdir','/tmp/a','--out','/tmp/b'])

    def test_continuation_rejects_changed_economics_and_duplicate_rule(self):
        import copy
        with tempfile.TemporaryDirectory() as d:
            ref, cur = Path(d)/'ref', Path(d)/'cur'
            ref.mkdir(); cur.mkdir()
            reg = dict(segments=['B','C'], shares=100, initial_cash=1000, latencies_ms=[500,600,1000],
                archives=[{'name':'day','sha256':'a'}], manifest_sha256='m', frozen_model_identity={'hash':'f'},
                dependencies_sha256={'cross.py':'c'}, auxiliary_inputs={'factor':'x'},rules=['R3'],
                code_sha256={'historical100_execution.py':'e','historical100_signals.py':'s'})
            for root in (ref,cur):
                R.write_json(root/'registration.json',reg)
                R.write_json(root/'markets.json',{'m':{'outcome':None}})
                R.write_json(root/'kline_provenance.json',{'hash':'k'})
                R.write_gz(root/'signals.jsonl.gz',[{'signal_id':'s','asklimit':.5}])
            self.assertTrue(R.verify_continuation(ref,cur)['verified'])
            for field in ('frozen_model_identity','archives','dependencies_sha256','auxiliary_inputs','shares'):
                bad=copy.deepcopy(reg);bad[field]='changed';R.write_json(cur/'registration.json',bad)
                with self.assertRaisesRegex(ValueError,'identity mismatch'):R.verify_continuation(ref,cur)
            R.write_json(cur/'registration.json',reg)
            bad=copy.deepcopy(reg);bad['code_sha256']['historical100_execution.py']='changed'
            R.write_json(cur/'registration.json',bad)
            with self.assertRaisesRegex(ValueError,'engine changed'):R.verify_continuation(ref,cur)
            bad['entry_fill_policy']='full100_or_zero'
            R.write_json(cur/'registration.json',bad)
            audit=R.verify_continuation(ref,cur)
            self.assertTrue(audit['signals_and_economic_inputs_unchanged'])
            self.assertFalse(audit['execution_and_signals_unchanged'])
            self.assertEqual(audit['execution_revision']['entry_fill_policy'],'full100_or_zero')
            R.write_json(cur/'registration.json',reg)
            R.write_gz(cur/'signals.jsonl.gz',[{'signal_id':'s','asklimit':.6}])
            with self.assertRaisesRegex(ValueError,'population changed'):R.verify_continuation(ref,cur)
            R.write_gz(cur/'signals.jsonl.gz',[{'signal_id':'s','asklimit':.5}])
            R.write_json(cur/'markets.json',{'m':{'outcome':'up'}})
            with self.assertRaisesRegex(ValueError,'data mismatch'):R.verify_continuation(ref,cur)
            R.write_json(cur/'markets.json',{'m':{'outcome':None}})
            R.write_json(ref/'R3_500ms_summary.json',{})
            with self.assertRaisesRegex(ValueError,'already has results'):R.verify_continuation(ref,cur)

    def test_epoch_units_and_invalid_epoch(self):
        self.assertEqual(R.timestamp_ms(1786900000), 1786900000000.)
        self.assertEqual(R.timestamp_ms(1786900000000), 1786900000000.)
        self.assertIsNone(R.timestamp_ms(12345))
        self.assertIsNone(R.timestamp_ms(float('nan')))

    def test_duplicate_grid_does_not_fabricate_recent_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'b.gz'
            def row(t, q):
                return {'recv_ms': t, 'up': {'bids': [[.4, q]], 'asks': [[.5, 100]]},
                        'down': {'bids': [[.4, 100]], 'asks': [[.5, 100]]}}
            R.write_gz(p, [row(100, 100), row(200, 100), row(300, 100), row(300, 90)])
            self.assertEqual(R.finalise_book(p), 3)
            with gzip.open(p, 'rt') as f:
                rows = [json.loads(x) for x in f]
            self.assertEqual([x['changed_ms'] for x in rows], [100, 100, 300])

    def test_final_resolution_overrides_periodic_label_and_delays_clock(self):
        import pandas as pd
        start = 1786924800000
        mk = pd.DataFrame([dict(market_id='m', slug='btc-updown-5m-1786924800',
            session_start_ts=start, session_end_ts=start + 300000,
            chainlink_open_price=1., up_won=1, up_token_id='u', down_token_id='d')])
        rs = pd.DataFrame([dict(market_id='m', horizon='5m', consistency_check='ok',
            outcome_direction='down', emitted_at_ts=(start + 500000) // 1000,
            revision=2)])
        _, markets, audit = R.official_markets(mk, rs)
        self.assertEqual(markets['m']['outcome'], 'down')
        self.assertEqual(markets['m']['outcome_known_ms'], start + 500000.)
        self.assertFalse(markets['m']['actual_redemption_clock_known'])
        self.assertEqual(audit['known_emitted_clock'], 1)

    def test_later_missing_resolution_cannot_erase_final(self):
        old = {'m': {'outcome': 'up', 'outcome_known_ms': 300.}}
        R.merge_markets(old, {'m': {'outcome': None, 'outcome_known_ms': None}})
        self.assertEqual(old['m']['outcome'], 'up')
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            R.merge_markets(old, {'m': {'outcome': 'down', 'outcome_known_ms': 400.}})

    def test_chunked_tar_reader_preserves_btc5m_depth_and_excludes_other_horizon(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            books = root / 'books'
            books.mkdir()
            rows = []
            for horizon, mid in [('5m', 'm'), ('15m', 'other')]:
                rows.append(dict(timestamp_ms=1786924800100, market_id=mid,
                    slug=f'btc-updown-{horizon}-1786924800', lifecycle_state='active',
                    observed_halt_flag=False, up_best_bid=.4, up_best_ask=.5,
                    down_best_bid=.4, down_best_ask=.5,
                    up_side_bids=[.4, .3], up_bid_sizes=[120., 80.],
                    up_side_asks=[.5, .6], up_ask_sizes=[100., 50.],
                    down_side_bids=[.4], down_bid_sizes=[80.],
                    down_side_asks=[.5], down_ask_sizes=[90.]))
            raw = io.BytesIO()
            pq.write_table(pa.Table.from_pylist(rows), raw)
            arc = root / 'day.tar.gz'
            with tarfile.open(arc, 'w:gz') as tar:
                info = tarfile.TarInfo('dataset=polymarket_features_100ms/date=2026-08-17/p.parquet')
                info.size = len(raw.getvalue())
                tar.addfile(info, io.BytesIO(raw.getvalue()))
            feat, _, _, _, counts, mids = R.read_archive(arc, root, books)
            self.assertEqual(list(feat.market_id), ['m'])
            self.assertEqual(feat.up_ask_size.iloc[0], 100.)
            self.assertEqual(mids, {'m'})
            self.assertEqual(counts['btc5m_feature_rows'], 1)
            R.finalise_book(R.book_path(books, 'm'))
            tape = R.DiskBooks(books, {'m'})['m']
            self.assertEqual(tape[0]['up']['asks'], [[.5, 100.], [.6, 50.]])
            self.assertTrue(tape[0]['active'])
            self.assertFalse((root / 'one_member.parquet').exists())


if __name__ == '__main__':
    unittest.main()
