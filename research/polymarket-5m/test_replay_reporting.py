"""Hand-computed account, censoring, and coverage cases for report exports."""
import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from replay_reporting import write_reports

START = 1791417600000  # 2026-10-08 00:00 UTC


def attempt(**overrides):
    row = {
        'candidate_id': 'PORT', 'latency_ms': 500, 'market_id': 'a',
        'decision_ms': START + 1_000, 'match_ms': START + 1_500,
        'requested_shares': 5, 'side': 'Up', 'submitted': True,
        'status': 'filled_partial', 'filled_shares': 3.0,
        'fees_usd': .05, 'extra_cost_usd': 0.0, 'cost_usd': 1.25,
        'settled': True, 'settlement_ms': START + 300_000,
        'payout_usd': 3.0, 'net_pnl_usd': 1.75,
        'cash_before_decision': 10.0, 'reserved_usd_at_decision': 2.10,
        'cash_after_match': 8.75, 'cash_after_settlement': 8.75,
        'cash_release_due_ms': START + 360_000, 'cash_released': True,
        'cash_after_release': 11.75,
    }
    row.update(overrides)
    return row


def result(rows, **overrides):
    filled = [r for r in rows if r['filled_shares'] > 0]
    unresolved = [r for r in filled if not r['settled']]
    settled = [r for r in filled if r['settled']]
    qty = sum(r['filled_shares'] for r in filled)
    pnl = None if unresolved else sum(r['net_pnl_usd'] for r in settled)
    value = {'capital_scope': 'one shared $10 portfolio per latency; do not sum',
             'attempts': rows,
             'summary': [{'candidate_id': 'PORT', 'latency_ms': 500,
                          'status': 'incomplete_unresolved_or_truncated' if unresolved else 'simulated_complete',
                          'attempts': len(rows), 'submitted_orders': sum(r['submitted'] for r in rows),
                          'full_fills': sum(r['status'] == 'filled_full' for r in rows),
                          'partial_fills': sum(r['status'] == 'filled_partial' for r in rows),
                          'filled_shares': qty, 'fees_usd': sum(r['fees_usd'] for r in rows),
                          'settled_positions': len(settled), 'unresolved_positions': len(unresolved),
                          'net_pnl_usd': pnl, 'net_pnl_per_filled_share_usd': pnl / qty if pnl is not None and qty else None,
                          'initial_cash_usd': 10, 'ending_cash_usd': 11.75,
                          'max_realized_drawdown_usd': 0}]}
    value.update(overrides)
    return value


def manifest():
    return {'markets': [{'market_id': 'a', 'start_ms': START, 'end_ms': START + 300_000}],
            'daily': [{'utc_date': '2026-10-08', 'recorded_markets': 1, 'fully_recorded_markets': 1,
                       'coverage_complete': False}]}


def csv_rows(path):
    with open(path, encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


class ReportsTests(unittest.TestCase):
    def test_partial_fill_cash_release_and_rejection(self):
        rejection = attempt(market_id='b', submitted=False, status='rejected_insufficient_available_cash',
                            filled_shares=0, cost_usd=0, fees_usd=0, settled=False,
                            payout_usd=None, net_pnl_usd=None, reserved_usd_at_decision=0,
                            cash_after_match=None, cash_after_settlement=None,
                            decision_ms=START+2_000, match_ms=START+2_500)
        rows = [attempt(), rejection]
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result(rows), manifest(), {})
            m = csv_rows(paths['metrics'])[0]
            self.assertEqual((m['first_signals'], m['submitted_orders'], m['partial_fills'], m['failed_or_unfilled_attempts']), ('2', '1', '1', '1'))
            self.assertEqual(float(m['net_pnl_usd']), 1.75)
            self.assertAlmostEqual(float(m['net_pnl_per_filled_share_usd']), 1.75/3)
            cash = csv_rows(paths['cash_ledger'])
            self.assertAlmostEqual(10 + sum(float(r['cash_delta_usd']) for r in cash), 11.75)
            self.assertAlmostEqual(sum(float(r['reserved_delta_usd']) for r in cash), 0)
            self.assertAlmostEqual(sum(float(r['locked_payout_delta_usd']) for r in cash), 0)
            settlement = next(r for r in cash if r['event_type'] == 'settlement_recognition')
            release = next(r for r in cash if r['event_type'] == 'payout_cash_release')
            self.assertEqual(float(settlement['cash_delta_usd']), 0)
            self.assertEqual(float(settlement['locked_payout_delta_usd']), 3)
            self.assertEqual(float(release['cash_delta_usd']), 3)
            audit = json.loads(Path(paths['audit']).read_text())
            self.assertEqual(audit['reporting']['reconciliation_differences'], [])
            self.assertEqual(audit['reporting']['market_start_unknown_attempts'], 1)
            with gzip.open(paths['attempts'], 'rt') as handle:
                self.assertEqual([json.loads(line) for line in handle], rows)

    def test_unresolved_position_stays_null_not_zero(self):
        unresolved = attempt(market_id='b', status='filled_full', filled_shares=5,
                             cost_usd=2.5, fees_usd=0, settled=False, payout_usd=None, net_pnl_usd=None)
        data = manifest()
        data['markets'].append({'market_id': 'b', 'start_ms': START+300_000, 'end_ms': START+600_000})
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result([attempt(), unresolved]), data, {})
            m, d = csv_rows(paths['metrics'])[0], csv_rows(paths['daily'])[0]
            self.assertEqual(m['net_pnl_usd'], '')
            self.assertEqual(d['net_pnl_usd'], '')
            self.assertEqual(d['net_pnl_per_filled_share_usd'], '')
            self.assertEqual(float(d['realized_pnl_usd']), 1.75)
            self.assertEqual(d['unresolved_positions'], '1')
            self.assertIn('null', Path(paths['report']).read_text())

    def test_market_day_does_not_follow_late_outcome_cash_clock(self):
        row = attempt(settlement_ms=START + 86_500_000, cash_release_due_ms=START + 86_600_000)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result([row]), manifest(), {})
            self.assertEqual(csv_rows(paths['daily'])[0]['market_start_utc_date'], '2026-10-08')
            cash = csv_rows(paths['cash_ledger'])
            self.assertEqual(next(r['event_utc_date'] for r in cash if r['event_type'] == 'settlement_recognition'), '2026-10-09')

    def test_seven_calendar_days_and_zero_signals_do_not_fake_coverage(self):
        data = manifest()
        data['requested_interval_ms'] = [START, START + 7 * 86_400_000]
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result([attempt()]), data, {})
            days = csv_rows(paths['daily'])
            self.assertEqual(len(days), 7)
            self.assertEqual(sum(int(d['expected_calendar_slots']) for d in days), 2016)
            self.assertTrue(all(d['coverage_complete'] == 'False' for d in days))
            missing = next(d for d in days if d['market_start_utc_date'] == '2026-10-09')
            self.assertEqual(missing['status'], 'no_signal_observed')
            self.assertEqual(missing['net_pnl_usd'], '')
            self.assertEqual(missing['fully_recorded_markets'], '')

    def test_two_latencies_never_aggregate_cash_or_profit(self):
        a, b = attempt(), attempt(latency_ms=1000)
        base = result([a])
        s2 = dict(base['summary'][0], latency_ms=1000)
        base['summary'].append(s2)
        base['attempts'].append(b)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, base, manifest(), {})
            metrics = csv_rows(paths['metrics'])
            self.assertEqual(len(metrics), 2)
            self.assertTrue(all(float(m['net_pnl_usd']) == 1.75 for m in metrics))
            self.assertTrue(all(float(m['initial_cash_usd']) == 10 for m in metrics))
            self.assertTrue(all('do not sum' in m['capital_scope'] for m in metrics))

    def test_engine_ordered_cash_events_are_not_reconstructed(self):
        events = [{'candidate_id': 'PORT', 'latency_ms': 500, 'event_sequence': 7,
                   'event_ms': START, 'event_type': 'reserve', 'cash_usd': 10, 'reserved_usd': 2.1},
                  {'candidate_id': 'PORT', 'latency_ms': 500, 'event_sequence': 8,
                   'event_ms': START, 'event_type': 'fill', 'cash_usd': 8.75, 'reserved_usd': 0}]
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result([attempt()], cash_events=events), manifest(), {})
            cash = csv_rows(paths['cash_ledger'])
            self.assertEqual([r['event_sequence'] for r in cash], ['7', '8'])
            self.assertEqual([r['cash_usd'] for r in cash], ['10', '8.75'])
            self.assertTrue(all(r['report_cash_basis'] == 'engine_recorded_event' for r in cash))

    def test_unknown_market_date_not_guessed_from_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result([attempt()]), {}, {})
            self.assertEqual(csv_rows(paths['daily'])[0]['market_start_utc_date'], 'unknown')
            self.assertEqual(csv_rows(paths['daily'])[0]['expected_calendar_slots'], '')

    def test_intake_nested_coverage_keeps_span_distinct_from_health(self):
        data = {'coverage': {'markets': manifest()['markets'],
                            'daily': [{'date_utc': '2026-10-08', 'expected_slots': 288,
                                       'registered_markets': 1, 'recorded_span_slots': 2,
                                       'complete_span_slots': 1, 'missing_span_slots': 286,
                                       'observed_seconds': 400, 'book_markets': 1}],
                            'complete_feed_health_verified': False}}
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, result([attempt()]), data, {})
            day = csv_rows(paths['daily'])[0]
            self.assertEqual(day['market_start_utc_date'], '2026-10-08')
            self.assertEqual(day['expected_calendar_slots'], '288')
            self.assertEqual(day['complete_span_slots'], '1')
            self.assertEqual(day['fully_recorded_markets'], '')
            self.assertEqual(day['coverage_complete'], 'False')
            self.assertIn('完整跨度时段', Path(paths['report']).read_text())

    def test_duplicate_summary_scenarios_rejected(self):
        base = result([attempt()])
        base['summary'].append(base['summary'][0])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'separate report directories'):
                write_reports(directory, base, manifest(), {})

    def test_summary_discrepancy_is_exposed(self):
        base = result([attempt()])
        base['summary'][0]['fees_usd'] = 10
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(directory, base, manifest(), {})
            audit = json.loads(Path(paths['audit']).read_text())
            self.assertEqual(audit['reporting']['reconciliation_differences'][0]['field'], 'fees_usd')


if __name__ == '__main__':
    unittest.main()
