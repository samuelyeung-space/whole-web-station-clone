"""Observer invariance and bounded trace tests; synthetic, not PnL evidence."""
import unittest
import gzip
import json
import tempfile
from pathlib import Path
import complement_queue_lag as lag
from bbo_consistency_diagnostic import BboAudit, first_depth_witness
from test_complement_queue_lag_failclosed import fixture


class DiagnosticTests(unittest.TestCase):
    def test_observer_does_not_change_valid_execution_or_counters(self):
        events, maps, delta = fixture()
        for row in events:
            if row['kind'] == 'clob_price_change' and row['asset_id'] == 'down':
                row.update(best_bid=.46, best_ask=.48)
        expected = lag.replay(events, maps, {'m1':'Down'}, segment_end_ms=101000)
        audit = BboAudit()
        actual = lag.replay(events, maps, {'m1':'Down'}, segment_end_ms=101000,
                            bbo_diagnostic_hook=audit.observe)
        self.assertEqual(actual, expected)
        self.assertEqual(audit.counts['matched'], 1)

    def test_initial_mismatch_and_latched_followups_are_separate(self):
        events, maps, delta = fixture()
        bad = delta('down', 100300, 7, 'SELL', .48, 9)
        bad.update(best_bid=.46, best_ask=.49)
        follow = delta('down', 100350, 8, 'BUY', .46, 9)
        follow.update(best_bid=.46, best_ask=.49)
        events += [bad, follow]
        audit = BboAudit(1)
        expected = lag.replay(events, maps, {'m1':'Down'}, segment_end_ms=101000)
        actual = lag.replay(events, maps, {'m1':'Down'}, segment_end_ms=101000,
                            bbo_diagnostic_hook=audit.observe)
        self.assertEqual(actual, expected)
        self.assertEqual(audit.counts['depth_available/ask'], 1)
        self.assertEqual(audit.counts['awaiting_snapshot/both'], 1)
        self.assertTrue(all(r['filled_shares'] == 0 for r in actual[0]))

    def test_bounded_examples_keep_all_counts(self):
        audit = BboAudit(2)
        for i in range(100):
            audit.observe(dict(bid_matches=False, ask_matches=True,
                snapshot_required_before_check=False, recv_ms=i))
        self.assertEqual(audit.result()['counts']['mismatched'], 100)
        self.assertEqual(len(audit.examples['depth_available/bid']), 2)

    def test_invalid_caps_fail_closed(self):
        for cap in (-1, 21):
            with self.assertRaises(ValueError):
                BboAudit(cap)

    def test_witness_is_capped_and_contains_no_later_event(self):
        audit = BboAudit(1)
        audit.observe(dict(bid_matches=False, ask_matches=True,
            snapshot_required_before_check=False, recv_ms=120))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'events.gz'
            with gzip.open(path, 'wt') as stream:
                for i in range(150):
                    stream.write(json.dumps(dict(recv_ms=100 + i/10, seq=i))+'\n')
                stream.write(json.dumps(dict(recv_ms=121, seq=150))+'\n')
            witness = first_depth_witness(path, audit)
        self.assertTrue(witness['truncated'])
        self.assertEqual(witness['matching_raw_rows'], 150)
        self.assertEqual(len(witness['retained_rows']), 100)
        self.assertTrue(all(x['event']['recv_ms'] <= 120 for x in witness['retained_rows']))


if __name__ == '__main__':
    unittest.main()
