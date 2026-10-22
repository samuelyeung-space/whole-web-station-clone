"""Independent row arithmetic and receipt-clock checks for the consumed run397104 audit.

Usage: python reconcile_results.py RESULT_DIRECTORY
This checks recorded simulated execution; it does not estimate private fills.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path


def close(a, b, tolerance=1e-7):
    assert math.isclose(float(a), float(b), rel_tol=0, abs_tol=tolerance), (a, b)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_race(root):
    result = json.loads((root / 'source_race_result2.json').read_text())
    output = {}
    for arm in ('baseline', 'candidate'):
        path = root / 'rows2' / (arm + '.jsonl')
        rows = read_rows(path)
        assert digest(path) == result['observation_files'][arm]['sha256']
        for row in rows:
            assert row['ordering_clock'] == 'receive_ms'
            assert row['match_book_clock'] == 'receive_ms'
            assert row['future_health_checked'] is False
            close(row['signal_ms'], row['signal_receive_ms'])
            close(row['evaluation_time_ms'], row['signal_receive_ms'] + row['evaluation_ms'], 1e-3)
            if row['book_receive_ms'] is not None:
                assert row['book_receive_ms'] <= row['evaluation_time_ms'] + 1e-6
            if row['filled']:
                assert row['sent'] and row['filled_shares'] > 0
                assert row['evaluation_time_ms'] >= row['earliest_match_ms'] - 1e-6
                assert row['fill_price'] <= row['fixed_limit'] + 1e-9
                close(row['all_in_cost'] * row['filled_shares'], row['fill_price'] * row['filled_shares'] + row['fill_fee'])
                if row.get('winner') is not None:
                    close(row['pnl'], (int(row['winner'] == row['direction']) - row['all_in_cost']) * row['filled_shares'])
            else:
                close(row['filled_shares'], 0)
        latencies = {}
        for delay, reported in result['arms'][arm]['latencies'].items():
            sample = [r for r in rows if r['evaluation_ms'] == float(delay)]
            fills = [r for r in sample if r['filled'] and r.get('winner') is not None]
            unresolved = [r for r in sample if r['filled'] and r.get('winner') is None]
            sent = sum(r['sent'] for r in sample)
            shares = math.fsum(r['filled_shares'] for r in fills)
            pnl = math.fsum(r['pnl'] for r in fills)
            total_cost = math.fsum(r['all_in_cost'] * r['filled_shares'] for r in fills)
            total_fees = math.fsum(r['fill_fee'] for r in fills)
            assert sent == reported['sent']
            assert len(fills) == reported['fills']
            close(shares, reported['filled_shares'])
            close(pnl, reported['pnl'])
            # The pinned summarize() collapses same-reason buckets from
            # different sources through a dict comprehension. Preserve that
            # frozen output, flag its deficit, and never invent a breakdown.
            reason_total = sum(reported['reasons'].values())
            assert reason_total <= reported['signals']
            assert reported['signals'] >= len(sample)
            latencies[delay] = {'signals': reported['signals'], 'recorded_sends': sent,
                'failed_sends': sent - sum(r['filled'] for r in sample),
                'resolved_fills': len(fills), 'unresolved_fills': len(unresolved),
                'shares': shares, 'resolved_pnl_usd': pnl,
                'filled_markets': len({r['market_id'] for r in fills}),
                'simulated_fee_usd': total_fees,
                'filled_principal_and_fee_usd': total_cost,
                'no_payout_reuse_cash_floor_from_1000_usd': 1000 - total_cost,
                'cash_diagnostic_assumption': 'all resolved payouts withheld to segment end; small-size recorded fills only, no 100-share rerun',
                'complete_outcome_pnl_usd': None if unresolved else pnl,
                'share_weighted_net_cents': pnl / shares * 100 if shares else None,
                'reported_reason_total': reason_total,
                'reason_breakdown_missing_count': reported['signals'] - reason_total,
                'reason_breakdown_complete': reason_total == reported['signals'],
                'reasons': dict(Counter(r['reason'] for r in sample))}
        output[arm] = {'rows': len(rows), 'sha256': digest(path), 'latencies': latencies}
    assert result['formal_sample_eligible'] is False
    assert result['activation_gate']['passes'] is False
    return output


def family34(root, attempt='2'):
    result = json.loads((root / ('family34_result' + attempt + '.json')).read_text())
    row_path = root / ('family34_rows' + attempt + '.jsonl')
    rows = read_rows(row_path)
    assert len(rows) == result['record_count']
    output = {}
    for variant, reported_variant in result['variants'].items():
        latencies = {}
        for delay, reported in reported_variant['latencies'].items():
            sample = [r for r in rows if r.get('variant') == variant and r.get('evaluation_ms') == float(delay)]
            fills = [r for r in sample if r.get('filled')]
            resolved = [r for r in fills if r.get('pnl') is not None]
            unresolved = len(fills) - len(resolved)
            shares = math.fsum(r['filled_shares'] for r in resolved)
            pnl = math.fsum(r['pnl'] for r in resolved)
            assert len(fills) == reported['fills']
            assert unresolved == reported['unresolved_fills']
            close(pnl, reported['resolved_pnl'])
            close(shares, reported['resolved_shares'])
            if unresolved:
                assert reported['pnl'] is None
            else:
                close(pnl, reported['pnl'])
            latencies[delay] = {'attempts': reported['attempts'], 'fills': len(fills),
                'unresolved_fills': unresolved, 'resolved_shares': shares,
                'raw_output_pnl_usd': None if unresolved else pnl,
                'economic_result_pnl_usd': None if unresolved or not reported['attempts'] else pnl,
                'resolved_pnl_usd': pnl, 'share_weighted_net_cents': pnl / shares * 100 if shares else None}
        output[variant] = latencies
    assert result['promotion_eligible'] is False
    return {'rows': len(rows), 'sha256': digest(row_path), 'variants': output,
            'counters': result['counters'], 'classification': 'no_attempt_data_consistency_blocked' if not rows else 'development_only'}


if __name__ == '__main__':
    root = Path(sys.argv[1])
    print(json.dumps({'schema': 'run397104-independent-reconciliation-v1',
        'source_race': source_race(root), 'family34': family34(root),
        'family34_after_one_sided_fix': family34(root, '3'),
        'rank7_pnl_usd': None, 'formal_oos_result': None}, indent=2, allow_nan=False))
