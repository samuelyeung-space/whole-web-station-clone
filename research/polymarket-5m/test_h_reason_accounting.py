"""Synthetic cross-source collision tests, not a new strategy evaluation."""
import copy
import unittest
from h_reason_accounting import audit_reason_buckets


def sample():
    rows = [dict(evaluation_ms=500, reason='filled', signal_source='spot_trade')]
    counters = {'_suppressed_unsent': [
        dict(evaluation_ms=500, reason='rate', source='spot_trade', count=7),
        dict(evaluation_ms=500, reason='rate', source='deribit_quote', count=11)]}
    summary = {'latencies': {'500': dict(signals=19, sent=1, fills=1,
        filled_shares=5, pnl=1.25, exact_p=.4, reasons=dict(filled=1, rate=11))},
        'formal_sample_eligible': False}
    return rows, counters, summary


class ReasonAccountingTests(unittest.TestCase):
    def test_colliding_sources_are_added_and_financials_unchanged(self):
        rows, counters, summary = sample()
        old = copy.deepcopy(summary)
        result = audit_reason_buckets(rows, counters, summary)
        self.assertEqual(result['latencies']['500']['reasons'], dict(filled=1, rate=18))
        self.assertEqual(result['reason_accounting']['by_latency']['500']['legacy_missing_count'], 7)
        for key in ('signals','sent','fills','filled_shares','pnl','exact_p'):
            self.assertEqual(result['latencies']['500'][key], old['latencies']['500'][key])
        self.assertFalse(result['formal_sample_eligible'])
        self.assertEqual(summary, old)

    def test_already_compacted_result_cannot_invent_missing_buckets(self):
        rows, _, summary = sample()
        with self.assertRaisesRegex(ValueError, 'complete rows/counters required'):
            audit_reason_buckets(rows, {}, summary)

    def test_duplicate_buckets_fail(self):
        rows, counters, summary = sample()
        counters['_suppressed_unsent'].append(counters['_suppressed_unsent'][0].copy())
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            audit_reason_buckets(rows, counters, summary)

    def test_invalid_counts_fail(self):
        for value in (-1, True, 1.1):
            rows, counters, summary = sample()
            counters['_suppressed_unsent'][0]['count'] = value
            with self.assertRaises(ValueError):
                audit_reason_buckets(rows, counters, summary)

    def test_wrong_latency_fails(self):
        rows, counters, summary = sample()
        counters['_suppressed_unsent'][0]['evaluation_ms'] = 600
        with self.assertRaises(ValueError):
            audit_reason_buckets(rows, counters, summary)


if __name__ == '__main__':
    unittest.main()
