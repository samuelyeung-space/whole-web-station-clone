"""Synthetic audit counterexamples, not strategy performance evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import historical100_summary as S
from historical100_execution import replay
from test_historical100_execution import book, market, signal


def audit(result):
    return S.audit_scenario(result['attempts'], result['exits'], result['cash_events'],
                            result['summary'], result['config'])


class SummaryTests(unittest.TestCase):
    def test_partial_exit_and_settlement_reconcile_independently(self):
        r = replay({'m': market(outcome='down')},
            {'m': [book(1000), book(1500), book(61500, ask=.62, bid=.6, bid_qty=30),
                   book(62000, ask=.63, bid=.61, bid_qty=30)]},
            [signal(rule='R2')], rule_id='R2', latency_ms=500)
        self.assertEqual(audit(r)['discrepancy_count'], 0)

    def test_reservation_rejections_and_unknown_outcome(self):
        r = replay({'m': market(outcome=None, known=None)},
            {'m': [book(1000, ask_qty=3000), book(1500, ask_qty=3000)]},
            [signal(str(i)) for i in range(25)], rule_id='R1', latency_ms=500)
        self.assertEqual(audit(r)['discrepancy_count'], 0)
        self.assertIsNone(S.aggregate(r['attempts'])['net_pnl_usd'])
        self.assertEqual(S.aggregate(r['attempts'])['unresolved_positions'], 19)

    def test_corrupted_fee_and_invented_cash_are_detected(self):
        r = replay({'m': market()}, {'m': [book(1000), book(1500)]},
                   [signal()], rule_id='R1', latency_ms=500)
        bad = deepcopy(r)
        bad['attempts'][0]['entry_fee_usd'] += 1
        bad['cash_events'][0]['available_cash_usd'] += 5
        fields = {x.get('field') for x in audit(bad)['discrepancies']}
        self.assertIn('entry_fee_usd', fields)
        self.assertIn('available_cash_usd', fields)

    def test_missing_actual_results_do_not_emit_zero(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'audit'
            with self.assertRaisesRegex(FileNotFoundError, 'actual metrics absent'):
                S.run(Path(d), out)
            self.assertFalse(out.exists())

    def test_one_cluster_is_not_confidence_interval(self):
        r = replay({'m': market()}, {'m': [book(1000), book(1500)]},
                   [signal()], rule_id='R1', latency_ms=500)
        result = S.cluster_uncertainty(r['attempts'], lambda a: a['market_id'])
        self.assertIsNone(result['ci95_total_pnl_usd'])
        self.assertEqual(result['clusters'], 1)


if __name__ == '__main__':
    unittest.main()
