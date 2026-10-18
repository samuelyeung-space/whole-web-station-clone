"""Descriptive attribution of existing development ledgers; never a replay or CI.

Removing a realised contribution does NOT recompute cash-constrained orders.
It is an arithmetic fragility diagnostic, not an executable counterfactual.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

ROLE = 'already_consumed_development_no_fitting'
SCENARIOS = ('base', 'stack_1h_reject10', 'stack_4h_reject30')
LATENCIES = (500, 600, 1000)
MONEY_RECONCILIATION_TOLERANCE = 1e-8


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def key(row):
    require(isinstance(row.get('candidate_id'), str) and row['candidate_id'], 'candidate identity missing')
    require(type(row.get('latency_ms')) is int and row['latency_ms'] in LATENCIES, 'latency identity invalid')
    require(row.get('scenario') in SCENARIOS, 'scenario identity invalid')
    return row['candidate_id'], row['latency_ms'], row['scenario']


def market_calendar(coverage):
    values = coverage.get('markets')
    require(isinstance(values, list) and values, 'explicit market calendar required')
    result = {}
    for row in values:
        mid, start, end = row.get('market_id'), row.get('start_ms'), row.get('end_ms')
        require(isinstance(mid, str) and mid and mid not in result, 'duplicate or invalid market identity')
        require(number(start) and number(end) and end-start == 300_000 and start % 300_000 == 0,
                'calendar must contain explicit UTC-aligned BTC5m slots')
        result[mid] = dict(start_ms=start, end_ms=end,
            utc_date=datetime.fromtimestamp(start/1000, timezone.utc).date().isoformat())
    return result


def contribution_stats(values, total):
    """Aggregate net contributions first; do not rank individual winning fills."""
    positives = sorted((value for value in values if value > 0), reverse=True)
    negatives = [value for value in values if value < 0]
    attributed_total = math.fsum(values)
    gross_positive = math.fsum(positives)
    count_to_erase = None
    if attributed_total > 0:
        for index in range(1, len(positives)+1):
            if math.fsum(positives[index:]+negatives) <= 0:
                count_to_erase = index
                break
    return dict(attributed_net_pnl_usd=attributed_total,
        attributed_minus_reported_net_usd=attributed_total-total,
        gross_positive_contributions_usd=gross_positive,
        gross_negative_contributions_usd=math.fsum(v for v in values if v < 0),
        positive_contribution_hhi=math.fsum((v/gross_positive)**2 for v in positives) if gross_positive else None,
        best_positive_contribution_usd=positives[0] if positives else 0.0,
        best_positive_fraction_of_gross_positive=positives[0]/gross_positive if positives else None,
        best_positive_fraction_of_net_pnl=positives[0]/total if positives and total > 0 else None,
        arithmetic_net_excluding_top_positive={str(n):math.fsum(positives[n:]+negatives) for n in (1,3,5)},
        positive_blocks_needed_to_erase_observed_net=count_to_erase)


def analyze(metrics, attempts, coverage, protocol):
    require(protocol.get('source_role') == ROLE, 'only explicitly consumed development is allowed')
    calendar = market_calendar(coverage)
    days = sorted({m['utc_date'] for m in calendar.values()})
    summaries = {}
    for row in metrics:
        k = key(row)
        require(k not in summaries, 'duplicate summary cell')
        require(row.get('effective_split') == 'development' and row.get('status') == 'simulated_complete',
                'incomplete or non-development cell')
        require(row.get('initial_cash_usd') == 1000 and row.get('requested_shares') == 100,
                'independent 1000-dollar / 100-share benchmark required')
        require(row.get('unresolved_positions') == 0 and row.get('missing_execution_inputs') is False,
                'unresolved or missing execution input')
        require(number(row.get('net_pnl_usd')), 'finite summary PnL required')
        summaries[k] = row
    require(bool(summaries), 'no summary cells')
    candidates = sorted({k[0] for k in summaries})
    require(set(summaries) == {(c,l,s) for c in candidates for l in LATENCIES for s in SCENARIOS},
            'complete common panel required; do not silently omit losing/missing scenarios')
    grouped = defaultdict(list)
    identities = set()
    for row in attempts:
        k = key(row)
        require(k in summaries, 'attempt has unknown summary cell')
        mid = row.get('market_id')
        require(mid in calendar, 'attempt market lacks explicit start metadata')
        when = row.get('decision_ms')
        market = calendar[mid]
        require(number(when) and market['start_ms'] <= when < market['end_ms'],
                'decision outside declared market interval')
        identity = (*k, mid, when)
        require(identity not in identities, 'duplicate first-intent attribution row')
        identities.add(identity)
        quantity, pnl = row.get('filled_shares'), row.get('net_pnl_usd')
        require(number(quantity) and quantity >= 0, 'invalid filled quantity')
        if quantity > 0:
            require(row.get('status') in ('filled_full','filled_partial') and number(pnl),
                    'filled row requires settled finite PnL')
        else:
            require(pnl is None or (number(pnl) and pnl == 0), 'unfilled row has invalid/nonzero PnL')
        grouped[k].append(row)
    cells, market_rows, daily_rows = [], [], []
    vectors = {}
    for k, summary in sorted(summaries.items()):
        rows = grouped[k]
        require(len(rows) == summary.get('attempts'), 'attempt count mismatch')
        require(dict(Counter(r['status'] for r in rows)) == summary.get('status_counts'), 'status counts mismatch')
        realised = math.fsum(r['net_pnl_usd'] or 0 for r in rows)
        require(abs(realised-summary['net_pnl_usd']) <= MONEY_RECONCILIATION_TOLERANCE, 'PnL reconciliation failed')
        quantity = math.fsum(r['filled_shares'] for r in rows)
        require(number(summary.get('filled_shares')) and abs(quantity-summary['filled_shares']) <= 1e-8,
                'share reconciliation failed')
        by_market, by_day = defaultdict(list), defaultdict(list)
        for row in rows:
            by_market[row['market_id']].append(row)
            by_day[calendar[row['market_id']]['utc_date']].append(row)
        base = dict(candidate_id=k[0], latency_ms=k[1], scenario=k[2])
        vector = {}
        for mid, records in sorted(by_market.items()):
            pnl = math.fsum(r['net_pnl_usd'] or 0 for r in records)
            vector[mid] = pnl
            market_rows.append(dict(**base, market_id=mid, utc_date=calendar[mid]['utc_date'],
                attempts=len(records), fills=sum(r['filled_shares'] > 0 for r in records), net_pnl_usd=pnl))
        daily_pnl = {}
        for day in days:
            records = by_day[day]
            pnl = math.fsum(r['net_pnl_usd'] or 0 for r in records)
            daily_pnl[day] = pnl
            daily_rows.append(dict(**base, market_start_utc_date=day, attempts=len(records),
                fills=sum(r['filled_shares'] > 0 for r in records), net_pnl_usd=pnl,
                full_recorded_day_verified=False))
        vectors[k] = vector
        cells.append(dict(**base, net_pnl_usd=summary['net_pnl_usd'],
            attribution_reconciliation_delta_usd=realised-summary['net_pnl_usd'],
            attempts=len(rows), fills=sum(r['filled_shares'] > 0 for r in rows),
            signal_markets=len(by_market), filled_markets=sum(any(r['filled_shares'] > 0 for r in rs) for rs in by_market.values()),
            pnl_market_start_dates=len(days), daily_net_pnl_usd=daily_pnl,
            market=contribution_stats(list(vector.values()), summary['net_pnl_usd']),
            day=contribution_stats(list(daily_pnl.values()), summary['net_pnl_usd'])))
    overlaps = []
    # All pairs, main500ms, each scenario separate. Exact realisation equality
    # is a descriptive duplicate flag, not proof that future rules are equal.
    for scenario in SCENARIOS:
        for index, first in enumerate(candidates):
            a = vectors[first,500,scenario]
            for second in candidates[index+1:]:
                b = vectors[second,500,scenario]
                active_a = {m for m,v in a.items() if v != 0}
                active_b = {m for m,v in b.items() if v != 0}
                common, union = active_a & active_b, active_a | active_b
                overlaps.append(dict(scenario=scenario,latency_ms=500,first=first,second=second,
                    nonzero_pnl_market_jaccard=len(common)/len(union) if union else None,
                    exactly_equal_realised_market_vector=all(a.get(m,0) == b.get(m,0) for m in set(a)|set(b))))
    return dict(schema='consumed-development-pnl-concentration-v1',cells=cells,
        market_attribution=market_rows,daily_attribution=daily_rows,main500_pairs=overlaps,
        source_role=ROLE,candidate_count=len(candidates),cell_count=len(cells),
        pnl_usd_is_existing_fee_net_not_new_replay=True,new_replays=0,new_candidates=0,
        new_mechanisms=0,seven_day_rank=None,seven_day_pnl_usd=None,formal_oos_result=None,
        uncertainty=dict(confidence_interval=None,p_value=None,
            reason='Few partial UTC days, adaptive prior search, dependent markets and stateful cash path; attribution is not inferential resampling'),
        warnings=['Deleting realised contributions does not recompute capital-constrained orders and is not a tradable counterfactual.',
            'Never add candidates, latency cells, stress scenarios or matched controls together.',
            'A concentration diagnostic cannot validate positive OOS or full recording coverage.'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('metrics','attempts','coverage','protocol','out'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args(argv)
    paths={name:getattr(args,name) for name in ('metrics','attempts','coverage','protocol')}
    values={name:json.loads(path.read_text()) for name,path in paths.items()}
    result=analyze(**values)
    result['input_sha256']={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    result['code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.out.mkdir(exist_ok=False,parents=True)
    for field in ('cells','market_attribution','daily_attribution','main500_pairs'):
        (args.out/(field+'.json')).write_text(json.dumps(result.pop(field),indent=2,allow_nan=False)+'\n')
    (args.out/'manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(cell_count=result['cell_count'],candidate_count=result['candidate_count'],new_replays=0)))


if __name__ == '__main__':
    main()
