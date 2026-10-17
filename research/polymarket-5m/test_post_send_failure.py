"""Synthetic execution counterexamples only; these are not strategy backtests."""
import copy
from dataclasses import replace
import unittest

import replay_engine as ex
from cash_release_audit import reconcile


def fixture(depth=100):
    return [
        dict(type='dataset', recv_ms=0, dataset_id='synthetic', split='development',
             provenance={'source': 'synthetic-test'}, coverage_end_ms=2000),
        dict(type='market', recv_ms=0, market_id='m', start_ms=0,
             end_ms=300000, label_source='gamma'),
        dict(type='book', recv_ms=100, market_id='m', up_asks=[[.5, depth]],
             down_asks=[[.5, depth]], healthy=True, fee_rate=.07),
        dict(type='decision', recv_ms=100, market_id='m', features={'tau_s': 299.9},
             feature_available_ms={'tau_s': 100}),
        dict(type='book', recv_ms=500, market_id='m', up_asks=[[.5, depth]],
             down_asks=[[.5, depth]], healthy=True, fee_rate=.07),
        dict(type='decision', recv_ms=700, market_id='m', features={'tau_s': 299.3},
             feature_available_ms={'tau_s': 700}),
        dict(type='outcome', recv_ms=300000, market_id='m', resolved_ms=300000,
             winner='Up', source='gamma'),
    ]


def run(rows=None, bps=10000, candidates=None, **kw):
    cfg = ex.ReplayConfig(shares=100, initial_cash_usd=1000,
                          latencies_ms=(500, 600, 1000), max_quote_age_ms=1000,
                          post_send_reject_bps=bps, **kw)
    return ex.replay_events(rows or fixture(), candidates or [dict(id='A', required_features=[])],
                            lambda c, f: 'Up', cfg)


class PostSendFailureTests(unittest.TestCase):
    def test_validation(self):
        for x in (True, -1, 10001, 1.0, '1000'):
            with self.subTest(x=x), self.assertRaises(ValueError):
                ex.ReplayConfig(post_send_reject_bps=x)
        for seed in ('', None, False):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                ex.ReplayConfig(post_send_reject_seed=seed)

    def test_same_intent_common_shock_and_no_future_dependence(self):
        intent = dict(market_id='m', decision_ms=100, side='Up')
        cfg = ex.ReplayConfig(post_send_reject_bps=3000)
        for i in range(300):
            a = dict(intent, market_id=str(i))
            b = dict(a, candidate_id='different', latency_ms=1000,
                     winner='Down', filled_shares=0, future_ask=.99)
            self.assertEqual(ex.post_send_reject(a, cfg), ex.post_send_reject(b, cfg))
            if ex.post_send_reject(a, replace(cfg, post_send_reject_bps=1000)):
                self.assertTrue(ex.post_send_reject(a, cfg))
            self.assertFalse(ex.post_send_reject(a, replace(cfg, post_send_reject_bps=0)))
            self.assertTrue(ex.post_send_reject(a, replace(cfg, post_send_reject_bps=10000)))

    def test_full_rejection_still_submits_consumes_first_signal_and_reconciles(self):
        result = run()
        self.assertEqual(len(result['attempts']), 3)
        self.assertTrue(all(a['submitted'] and a['status']=='rejected_post_send_stress'
                            and a['filled_shares']==0 for a in result['attempts']))
        self.assertTrue(all(s['ending_cash_usd']==1000 and s['ending_reserved_usd']==0
                            for s in result['summary']))
        self.assertEqual(reconcile(result['attempts'], result['cash_events'], result['summary'], 0)['status'], 'passed')

    def test_reservation_lives_until_receipt_proxy(self):
        result = run()
        for latency in (500, 600, 1000):
            events = [e for e in result['cash_events'] if e['latency_ms']==latency]
            self.assertEqual([e['event_type'] for e in events],
                             ['reservation', 'reservation_release', 'post_send_reject'])
            self.assertEqual([e['event_ms'] for e in events], [100, 100+latency, 100+latency])
            self.assertGreater(events[0]['reserved_usd'], 50)

    def test_independent_accounts_and_partial_FAK_unchanged_at_zero(self):
        result = run(fixture(40), 0, [dict(id='A', required_features=[]), dict(id='B', required_features=[])])
        self.assertEqual(len(result['summary']), 6)
        self.assertTrue(all(a['filled_shares']==40 and a['requested_shares']==100
                            and a['status']=='filled_partial' for a in result['attempts']))
        self.assertTrue(all(s['initial_cash_usd']==1000 for s in result['summary']))
        self.assertFalse(any(e['event_type']=='post_send_reject' for e in result['cash_events']))

    def test_rejection_must_not_hide_missing_match_coverage(self):
        rows = fixture(); rows[0]['coverage_end_ms']=550
        result = run(rows)
        self.assertTrue(all(a['status']=='incomplete_match_horizon' for a in result['attempts']))
        self.assertTrue(all(s['net_pnl_usd'] is None for s in result['summary']))

    def test_rejection_must_not_hide_missing_book(self):
        rows=fixture(); rows=[r for r in rows if not (r['type']=='book' and r['recv_ms']==500)]
        result=ex.replay_events(
            rows, [dict(id='A',required_features=[])], lambda c,f:'Up',
            ex.ReplayConfig(shares=100,latencies_ms=(500,),max_quote_age_ms=250,
                            post_send_reject_bps=10000))
        self.assertEqual(result['attempts'][0]['status'], 'rejected_stale_book_at_match')
        self.assertFalse(any(e['event_type']=='post_send_reject' for e in result['cash_events']))

    def test_cash_audit_detects_missing_and_spurious_rejection(self):
        result = run(); bad = copy.deepcopy(result['cash_events'])
        bad = [e for e in bad if e['event_type']!='post_send_reject']
        with self.assertRaisesRegex(ValueError, 'missing/spurious'):
            reconcile(result['attempts'], bad, result['summary'], 0)
        bad = copy.deepcopy(result['attempts']); bad[0]['fees_usd']=1
        with self.assertRaises(ValueError):
            reconcile(bad, result['cash_events'], result['summary'], 0)


if __name__=='__main__':
    unittest.main()
