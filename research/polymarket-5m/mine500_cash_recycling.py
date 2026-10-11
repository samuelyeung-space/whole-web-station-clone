"""Development-only cash recycling stress on a complete fixed-size potential tape.

No strategy fit, no new market matching, no real redemption observations. Every
baseline decision must have been submitted, so all potential executions exist.
Only a causal cash check can suppress an order. Unchanged public-book potential
fills assume no own-order market impact. Never use this as formal OOS admission.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path

DELAYS_SECONDS = (0, 60, 300, 900, 3600, 14400, 86400)
SCENARIOS = ('fixed100', 'fixed100_extra_1c', 'fixed100_half_depth')


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def read_rows(path):
    with gzip.open(path, 'rt') as f:
        return [json.loads(line) for line in f if line.strip()]


def validate(rows):
    seen = set()
    for r in rows:
        key = r['market_id']
        if key in seen:
            raise ValueError('requires one first signal per market')
        seen.add(key)
        if not r['submitted']:
            raise ValueError('missing counterfactual match: baseline was not submitted')
        for k in ('decision_ms', 'match_ms', 'cost_usd',
                  'filled_shares', 'fees_usd', 'reserved_usd_at_decision'):
            if not math.isfinite(r[k]) or r[k] < 0:
                raise ValueError('invalid numeric field: ' + k)
        if r['requested_shares'] != 100 or r['match_ms'] < r['decision_ms']:
            raise ValueError('requires fixed100 and causal match time')
        if r['cost_usd'] > r['reserved_usd_at_decision'] + 1e-7:
            raise ValueError('cost exceeds causal reservation')
        if r['filled_shares'] > 0:
            if r['payout_usd'] is None or not math.isfinite(r['payout_usd']):
                raise ValueError('missing payout for filled position')
            if not r['settled'] or r['settlement_ms'] < r['match_ms']:
                raise ValueError('requires complete causal outcome for each potential fill')
            if r['payout_usd'] not in (0, r['filled_shares']):
                raise ValueError('requires binary final payout')
        elif r['cost_usd'] or r['payout_usd'] or r['fees_usd']:
            raise ValueError('nonzero cash on no fill')


def replay(rows, delay_seconds, initial_cash=1000.):
    """Process reserve, match, outcome information, then spendability separately.

    Same timestamp priority follows original cash audit: decision before match
    before information before payout. Last-decision snapshot is after all events
    at that timestamp; terminal cash assumes all specified redemptions complete.
    Outcome recognition marks the receivable at final payout, not market value.
    """
    validate(rows)
    if not math.isfinite(delay_seconds) or delay_seconds < 0 or initial_cash <= 0:
        raise ValueError('invalid cash or delay')
    events = [(r['decision_ms'], 0, i, 'decision') for i, r in enumerate(rows)]
    heapq.heapify(events)
    cash, reserved, open_cost, receivable = initial_cash, 0., 0., 0.
    realized, fees, cost_sum, payout_sum, shares = 0., 0., 0., 0., 0.
    peak_equity, max_dd, min_available = initial_cash, 0., initial_cash
    peak_receivable, peak_open, peak_reserved = 0., 0., 0.
    accepted, counts, ledger, decisions, daily = set(), Counter(), [], [], {}
    horizon = max((r['decision_ms'] for r in rows), default=0.)
    snapshot = None
    def balances():
        return dict(cash_usd=cash, reserved_usd=reserved,
                    available_usd=cash-reserved, unsettled_cost_usd=open_cost,
                    payout_receivable_usd=receivable, realized_pnl_usd=realized)
    while events:
        when, _, i, kind = heapq.heappop(events)
        if snapshot is None and when > horizon:
            snapshot = balances()
        r = rows[i]
        if kind == 'decision':
            # Only decision-time reservation and present available cash are read.
            need = r['reserved_usd_at_decision']
            admit = cash-reserved+1e-9 >= need
            decisions.append(dict(market_id=r['market_id'], decision_ms=when,
                admitted=admit, available_before_usd=cash-reserved,
                required_reservation_usd=need, original_status=r['status']))
            if admit:
                accepted.add(i)
                counts['submitted'] += 1
                reserved += need
                heapq.heappush(events, (r['match_ms'], 1, i, 'match'))
            else:
                counts['rejected_insufficient_cash'] += 1
        elif kind == 'match':
            reserved -= r['reserved_usd_at_decision']
            cash -= r['cost_usd']
            cost_sum += r['cost_usd']
            fees += r['fees_usd']
            shares += r['filled_shares']
            counts[r['status']] += 1
            if r['filled_shares'] > 0:
                counts['filled_orders'] += 1
                open_cost += r['cost_usd']
                heapq.heappush(events, (r['settlement_ms'], 2, i, 'information'))
        elif kind == 'information':
            open_cost -= r['cost_usd']
            receivable += r['payout_usd']
            net = r['payout_usd'] - r['cost_usd']
            realized += net
            peak_equity = max(peak_equity, initial_cash+realized)
            max_dd = max(max_dd, peak_equity-initial_cash-realized)
            day = datetime.fromtimestamp(r['decision_ms']/1000, timezone.utc).date().isoformat()
            daily[day] = daily.get(day, 0.) + net
            if r['payout_usd']:
                heapq.heappush(events, (when+delay_seconds*1000, 3, i, 'spendable'))
        else:
            cash += r['payout_usd']
            receivable -= r['payout_usd']
            payout_sum += r['payout_usd']
        if min(cash-reserved, reserved, open_cost, receivable) < -1e-6:
            raise AssertionError('negative cash, reservation or outstanding asset')
        if abs(cash+open_cost+receivable-initial_cash-realized) > 1e-6:
            raise AssertionError('cash conservation failed')
        min_available = min(min_available, cash-reserved)
        peak_receivable = max(peak_receivable, receivable)
        peak_open = max(peak_open, open_cost)
        peak_reserved = max(peak_reserved, reserved)
        ledger.append(dict(ts_ms=when, event=kind, market_id=r['market_id'], **balances()))
    if snapshot is None:
        snapshot = balances()
    if max(abs(reserved), abs(open_cost), abs(receivable),
           abs(cash-initial_cash-realized)) > 1e-6:
        raise AssertionError('terminal reconciliation failed')
    result = dict(delay_seconds=delay_seconds, initial_cash_usd=initial_cash,
        attempts=len(rows), counts=dict(counts), filled_shares=shares,
        fees_usd=fees, total_execution_cost_usd=cost_sum,
        total_received_payout_usd=payout_sum, net_pnl_usd=realized,
        terminal_cash_after_assumed_redemptions_usd=cash,
        minimum_available_cash_usd=min_available,
        peak_pending_redemption_usd=peak_receivable,
        peak_unsettled_cost_usd=peak_open, peak_reserved_usd=peak_reserved,
        max_outcome_realized_drawdown_usd=max_dd,
        drawdown_is_mark_to_market=False,
        last_decision_ms=horizon, last_decision_snapshot=snapshot,
        last_assumed_event_ms=ledger[-1]['ts_ms'] if ledger else None,
        daily_pnl_by_signal_date_utc=daily, pnl_per_filled_share=realized/shares if shares else None,
        actual_redemption_validated=False, formal_oos_result=None)
    return result, decisions, ledger


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--development', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    inputs = {s: a.development/(s+'_attempts.jsonl.gz') for s in SCENARIOS}
    registration = dict(registered_at_utc=datetime.now(timezone.utc).isoformat(),
        candidate_id='EPM-matched_market-02', evidence_class='consumed_development_cash_stress',
        strategy_candidates_added=0, refits=0, input_hashes={s:digest(p) for s,p in inputs.items()},
        code_sha256=digest(__file__), delays_seconds=DELAYS_SECONDS,
        latencies_ms=[500,600,1000], scenarios=SCENARIOS, initial_cash_usd=1000,
        requested_shares=100, new_cash_stress_alternatives=54, baseline_parity_checks=9,
        freeze_modified=False, actual_cash_delay_distribution_known=False,
        fees='Original observed trading fee parameters retained; no invented gas charge. Extra1c scenario separate.',
        assumptions=['No market impact; public-book potential fills remain valid when earlier orders are skipped.',
                     'No retry, no downsize, one original first signal per market.',
                     'Added delay starts at official outcome receipt/resolved clock, not market close.',
                     'Terminal payout may occur after the observed decision horizon; it is an assumed redemption, not observed cash.',
                     'Only reused development data; frozen prospective protocol unchanged.'])
    write(a.out/'registration.json', registration)  # Before any grid result.
    results, parity = [], []
    with gzip.open(a.out/'cash_events.jsonl.gz','wt') as ef, gzip.open(a.out/'decisions.jsonl.gz','wt') as df:
        for s, p in inputs.items():
            all_rows = read_rows(p)
            for lag in (500,600,1000):
                rows = [r for r in all_rows if r['latency_ms']==lag]
                for delay in DELAYS_SECONDS:
                    result, decisions, events = replay(rows, delay)
                    key = dict(scenario=s, latency_ms=lag, delay_seconds=delay)
                    results.append(dict(**result, scenario=s, latency_ms=lag))
                    for x in events: ef.write(json.dumps(dict(**x, **key))+'\n')
                    for x in decisions: df.write(json.dumps(dict(**x, **key))+'\n')
                    if delay == 0:
                        expected = sum(r['net_pnl_usd'] or 0 for r in rows)
                        error = abs(result['net_pnl_usd']-expected)
                        assert error < 1e-6 and not result['counts'].get('rejected_insufficient_cash',0)
                        parity.append(dict(**key, net_error_usd=error))
    write(a.out/'metrics.json', results)
    write(a.out/'baseline_parity.json', parity)
    print(json.dumps(dict(completed_alternatives=len(results), new_alternatives=54,
                         baseline_parity_checks=len(parity), formal_oos_result=None)))


if __name__ == '__main__':
    main()
