"""Independent event-by-event cash, receivable and reservation reconciliation.

Consumes completed offline replay reports only; no orders, fitting or signals.
Pending payouts remain assets, not spendable cash or forced terminal redemptions.
"""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import json
import math
from pathlib import Path

TOL = 1e-6


def close(actual, expected, name):
    if not math.isfinite(actual) or abs(actual-expected) > TOL:
        raise ValueError(f'{name}: {actual} != {expected}')


def reconcile(attempts, events, metrics, delay_ms):
    lookup = {str(r['attempt_id']): r for r in attempts}
    if len(lookup) != len(attempts):
        raise ValueError('duplicate attempt ID')
    summaries = {(r['candidate_id'], r['latency_ms']): r for r in metrics}
    states = {k: dict(cash=r['initial_cash_usd'], reserved=0., locked=0.,
                     realized=0., open_cost=0., peak_locked=0., min_available=r['initial_cash_usd'],
                     first_cash_shortage_ms=None) for k, r in summaries.items()}
    seen, clocks, sequence = defaultdict(Counter), {}, 0
    for e in events:
        seq, now, aid = int(e['event_sequence']), float(e['event_ms']), str(e['attempt_id'])
        if seq <= sequence:
            raise ValueError('nonmonotone global cash event sequence')
        sequence = seq
        r = lookup[aid]; key = (e['candidate_id'], int(e['latency_ms']))
        if key != (r['candidate_id'], r['latency_ms']):
            raise ValueError('attempt/account mismatch')
        if now < clocks.get(key, -math.inf):
            raise ValueError('nonmonotone account clock')
        clocks[key] = now
        s = states[key]; kind = e['event_type']; seen[aid][kind] += 1
        if seen[aid][kind] != 1:
            raise ValueError('duplicate cash event per attempt')
        if kind == 'reservation':
            close(now, r['decision_ms'], 'reservation clock')
            if not r['submitted'] or s['cash']-s['reserved']+TOL < r['reserved_usd_at_decision']:
                raise ValueError('reservation spends unavailable collateral')
            s['reserved'] += r['reserved_usd_at_decision']
        elif kind == 'reservation_release':
            close(now, r['match_ms'], 'match clock')
            if not seen[aid]['reservation']:
                raise ValueError('release without reservation')
            s['reserved'] -= r['reserved_usd_at_decision']
        elif kind in ('fill', 'no_fill'):
            close(now, r['match_ms'], 'execution clock')
            if not seen[aid]['reservation_release'] or (kind == 'fill') != (r['filled_shares'] > 0):
                raise ValueError('fill/reservation identity mismatch')
            s['cash'] -= r['cost_usd']; s['open_cost'] += r['cost_usd']
        elif kind == 'post_send_reject':
            close(now, r['match_ms'], 'rejection receipt-proxy clock')
            if (not seen[aid]['reservation_release']
                    or r['status'] != 'rejected_post_send_stress' or not r['submitted']
                    or any(r[name] != 0 for name in ('filled_shares', 'fees_usd', 'cost_usd'))
                    or r['settled'] or r['net_pnl_usd'] != 0):
                raise ValueError('invalid post-send rejection lifecycle')
        elif kind == 'settlement':
            close(now, r['settlement_ms'], 'settlement clock')
            if not seen[aid]['fill'] or not r['settled']:
                raise ValueError('settlement without fill')
            close(r['cash_release_due_ms'], now+delay_ms, 'release due clock')
            close(r['net_pnl_usd'], r['payout_usd']-r['cost_usd'], 'settlement PnL')
            s['open_cost'] -= r['cost_usd']; s['realized'] += r['net_pnl_usd']
            if delay_ms:
                s['locked'] += r['payout_usd']
            else:
                s['cash'] += r['payout_usd']
        elif kind == 'payout_release':
            close(now, r['cash_release_due_ms'], 'spendability clock')
            if not delay_ms or not seen[aid]['settlement'] or not r['cash_released']:
                raise ValueError('premature or unexpected payout release')
            s['cash'] += r['payout_usd']; s['locked'] -= r['payout_usd']
        else:
            raise ValueError('unknown cash event: '+kind)
        for col, field in [('cash_usd','cash'), ('reserved_usd','reserved'),
                           ('locked_payout_usd','locked'), ('realized_pnl_usd','realized')]:
            close(float(e[col]), s[field], col)
        if min(s['cash']-s['reserved'], s['reserved'], s['locked'], s['open_cost']) < -TOL:
            raise ValueError('negative available cash or asset')
        close(s['cash']+s['locked']+s['open_cost'], summaries[key]['initial_cash_usd']+s['realized'],
              'cash + receivable + open cost conservation (reservation not double charged)')
        s['peak_locked'] = max(s['peak_locked'], s['locked'])
        s['min_available'] = min(s['min_available'], s['cash']-s['reserved'])
    for r in attempts:
        aid = str(r['attempt_id']); counts = seen[aid]
        if counts['reservation'] != int(r['submitted']) or counts['reservation_release'] != int(r['submitted']):
            raise ValueError('missing reservation lifecycle')
        if counts['fill'] != int(r['filled_shares'] > 0) or counts['settlement'] != int(r['settled']):
            raise ValueError('missing fill/settlement lifecycle')
        if counts['post_send_reject'] != int(r['status'] == 'rejected_post_send_stress'):
            raise ValueError('missing/spurious post-send rejection event')
        expected_release = bool(r['settled'] and r['payout_usd'] and delay_ms and r['cash_released'])
        if counts['payout_release'] != int(expected_release):
            raise ValueError('missing/double payout')
        if r['status'] == 'rejected_insufficient_available_cash':
            key = (r['candidate_id'], r['latency_ms']); s = states[key]
            current = s['first_cash_shortage_ms']
            s['first_cash_shortage_ms'] = min(current, r['decision_ms']) if current is not None else r['decision_ms']
    accounts = []
    for key, s in states.items():
        metric = summaries[key]
        for field, actual in [('ending_cash_usd',s['cash']),('ending_locked_payout_usd',s['locked']),
                              ('ending_reserved_usd',s['reserved']),('realized_pnl_usd',s['realized']),
                              ('min_available_cash_usd',s['min_available'])]:
            close(metric[field], actual, field)
        rows = [r for r in attempts if (r['candidate_id'],r['latency_ms']) == key]
        close(sum(r['fees_usd'] for r in rows), metric['fees_usd'], 'fee total')
        close(sum(r['filled_shares'] for r in rows), metric['filled_shares'], 'filled share total')
        accounts.append(dict(candidate_id=key[0],latency_ms=key[1],**s,
            rejected_insufficient_cash=sum(r['status']=='rejected_insufficient_available_cash' for r in rows),
            payout_is_observed_redemption=False))
    return dict(status='passed', attempts=len(attempts), cash_events=len(events),
        accounts=accounts, delay_ms=delay_ms, formal_oos_result=None)


def audit(directory, baseline=None):
    root=Path(directory)
    with gzip.open(root/'attempts.jsonl.gz','rt') as f:
        attempts=[json.loads(line) for line in f]
    with (root/'cash_ledger.csv').open() as f:
        events=list(csv.DictReader(f))
    with (root/'metrics.csv').open() as f:
        metrics=list(csv.DictReader(f))
    for r in metrics:
        for key in ('latency_ms','cash_release_delay_ms'):
            r[key]=int(r[key])
        for key in ('initial_cash_usd','ending_cash_usd','ending_locked_payout_usd',
                    'ending_reserved_usd','realized_pnl_usd','min_available_cash_usd','fees_usd','filled_shares'):
            r[key]=float(r[key])
    delays={r['cash_release_delay_ms'] for r in metrics}
    if len(delays)!=1:raise ValueError('one release delay per directory required')
    result=reconcile(attempts,events,metrics,delays.pop())
    if baseline:
        with gzip.open(Path(baseline)/'attempts.jsonl.gz','rt') as f:
            base=[json.loads(line) for line in f]
        def identity(rows):
            return {(r['candidate_id'],r['latency_ms'],r['market_id']):
                (r['decision_ms'],r['match_ms'],r['side'],r['limit_price'],r['requested_shares']) for r in rows}
        if identity(attempts)!=identity(base):raise ValueError('cash stress changed original first signal or fixed limit')
        result['unchanged_first_signals_and_limits']=len(attempts)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scenario',required=True)
    p.add_argument('--baseline');p.add_argument('--out',required=True)
    a=p.parse_args();value=audit(a.scenario,a.baseline)
    Path(a.out).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in value.items() if k!='accounts'}))
