"""Private FAK calibration v2: receipts, separate economics and cluster limits.

V1 is invalid for evidence: its shared net_pnl_per_share mispriced actual fills,
its iid intervals ignored market clustering, and its mandatory match timestamp
excluded unresolved/rejected receipts. V2 does not migrate V1 economics silently.
No signals, orders, deployments, or changes to the frozen mine500 evaluator.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
from typing import Any, Iterable, Mapping, Sequence

EPS = 1e-9
FINAL_STATUSES = frozenset({'filled_full', 'filled_partial', 'killed', 'rejected'})
ALL_STATUSES = FINAL_STATUSES | {'unknown'}


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    market_id: str
    decision_ms: float
    send_ms: float
    match_ms: float | None
    response_ms: float | None
    requested_shares: float
    paper_fill_shares: float
    actual_fill_shares: float | None
    actual_status: str
    receipt_source: str
    limit_price: float
    paper_fill_vwap: float | None = None
    paper_fees_usd: float | None = None
    actual_fill_vwap: float | None = None
    actual_fees_usd: float | None = None
    settlement_payout_per_share: float | None = None
    venue_hold_ms: float | None = None
    match_time_source: str | None = None
    match_clock_error_bound_ms: float | None = None

    @property
    def paper_full(self):
        return self.paper_fill_shares + EPS >= self.requested_shares

    @property
    def actual_full(self):
        return self.known and self.actual_fill_shares + EPS >= self.requested_shares

    @property
    def actual_any(self):
        return self.known and self.actual_fill_shares > EPS

    @property
    def known(self):
        return self.actual_status in FINAL_STATUSES

    @property
    def day(self):
        return datetime.fromtimestamp(self.decision_ms / 1000, timezone.utc).date().isoformat()

    def pnl(self, source):
        quantity = self.paper_fill_shares if source == 'paper' else self.actual_fill_shares
        if source == 'actual' and not self.known:
            return None
        if quantity == 0:
            return 0.0
        price, fees = getattr(self, source + '_fill_vwap'), getattr(self, source + '_fees_usd')
        if price is None or fees is None or self.settlement_payout_per_share is None:
            return None
        return quantity * (self.settlement_payout_per_share - price) - fees


def _number(row, field, optional=False):
    value = row.get(field)
    if optional and value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f'{field} must be numeric, not boolean')
    try:
        value = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{field} must be present and numeric') from exc
    if not math.isfinite(value):
        raise ValueError(f'{field} must be finite')
    return value


def parse_attempt(row: Mapping[str, Any]) -> Attempt:
    if not isinstance(row, Mapping):
        raise ValueError('attempt must be an object')
    if 'net_pnl_per_share' in row:
        raise ValueError('v1 shared net_pnl_per_share is invalid_for_evidence; supply separate fill prices and fees')
    values = {}
    for name in ('attempt_id', 'market_id'):
        value = row.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{name} must be a non-empty string')
        values[name] = value.strip()
    status = row.get('actual_status')
    if status not in ALL_STATUSES:
        raise ValueError('unsupported actual_status')
    if row.get('receipt_source') != 'private_order_response':
        raise ValueError('receipt_source must be private_order_response')
    values.update(actual_status=status, receipt_source='private_order_response')
    for name in ('decision_ms', 'send_ms', 'requested_shares', 'paper_fill_shares', 'limit_price'):
        values[name] = _number(row, name)
    for name in ('match_ms', 'response_ms', 'actual_fill_shares', 'paper_fill_vwap',
                 'paper_fees_usd', 'actual_fill_vwap', 'actual_fees_usd',
                 'settlement_payout_per_share', 'venue_hold_ms', 'match_clock_error_bound_ms'):
        values[name] = _number(row, name, optional=True)
    decision, send, match, response = [values[k] for k in ('decision_ms','send_ms','match_ms','response_ms')]
    if decision > send or (response is not None and response < send):
        raise ValueError('clocks must satisfy decision_ms <= send_ms <= response_ms when present')
    if status != 'unknown' and response is None:
        raise ValueError('final private status requires response_ms')
    if status in {'unknown', 'rejected', 'killed'} and match is not None:
        raise ValueError('no-fill/unresolved orders have no measured trade match_ms; keep null')
    values['match_time_source'] = row.get('match_time_source')
    if match is not None:
        if match < send or (response is not None and match > response):
            raise ValueError('clocks must satisfy send_ms <= match_ms <= response_ms when match is observed')
        if values['match_time_source'] != 'measured_clock_aligned_venue':
            raise ValueError('match_ms requires measured_clock_aligned_venue provenance; receipt time is not match time')
        bound = values['match_clock_error_bound_ms']
        if bound is None or bound < 0:
            raise ValueError('measured match requires nonnegative match_clock_error_bound_ms')
    elif values['match_time_source'] is not None or values['match_clock_error_bound_ms'] is not None:
        raise ValueError('match clock metadata without match_ms')
    hold = values['venue_hold_ms']
    if hold is not None and hold < 0:
        raise ValueError('venue_hold_ms must be nonnegative')
    if hold is not None and match is not None and match-send+values['match_clock_error_bound_ms']+EPS < hold:
        raise ValueError('measured send-to-match cannot be shorter than venue_hold_ms')
    requested, paper, actual = [values[k] for k in ('requested_shares','paper_fill_shares','actual_fill_shares')]
    if requested <= 0 or paper < 0 or paper > requested+EPS:
        raise ValueError('invalid requested or paper fill shares')
    if status == 'unknown':
        if actual is not None:
            raise ValueError('unknown actual_fill_shares must be null, never imputed zero')
    elif actual is None or not 0 <= actual <= requested+EPS:
        raise ValueError('final outcome requires bounded actual_fill_shares')
    if status == 'filled_full' and not math.isclose(actual, requested, abs_tol=EPS):
        raise ValueError('filled_full requires all requested shares')
    if status == 'filled_partial' and not EPS < actual < requested-EPS:
        raise ValueError('filled_partial requires a strict partial fill')
    if status in {'killed','rejected'} and actual != 0:
        raise ValueError('killed/rejected cannot contain a fill')
    if not 0 < values['limit_price'] < 1:
        raise ValueError('limit_price must be within (0,1)')
    for source, quantity in (('paper',paper),('actual',actual)):
        price, fees = values[source+'_fill_vwap'], values[source+'_fees_usd']
        if fees is not None and fees < 0:
            raise ValueError('fees must be nonnegative; rebates need a separate cash flow')
        if quantity is None or quantity == 0:
            if price is not None or fees not in (None,0):
                raise ValueError('unknown/zero fill cannot carry fill economics')
        elif price is not None and not 0 < price <= values['limit_price']+EPS:
            raise ValueError('fill price violates frozen BUY limit')
    if values['settlement_payout_per_share'] not in (None,0,1):
        raise ValueError('settlement payout must be null, 0 or 1; only hold-to-resolution BUY orders supported')
    return Attempt(**values)


def load_attempts(path):
    rows, seen = [], set()
    with Path(path).open(encoding='utf-8') as handle:
        for number,line in enumerate(handle,1):
            if not line.strip(): continue
            try: row = parse_attempt(json.loads(line))
            except (ValueError, TypeError) as exc: raise ValueError(f'{path}:{number}: {exc}') from exc
            if row.attempt_id in seen: raise ValueError(f'duplicate attempt_id {row.attempt_id}')
            seen.add(row.attempt_id); rows.append(row)
    return rows


def _quantile(values, q):
    if not values: return None
    values = sorted(values); index = (len(values)-1)*q
    lo,hi = math.floor(index),math.ceil(index)
    return values[lo] if lo==hi else values[lo]*(hi-index)+values[hi]*(index-lo)


def _rate(rows):
    predicted = [r for r in rows if r.paper_full]
    known = [r for r in predicted if r.known]
    full = sum(r.actual_full for r in known)
    unknown = len(predicted)-len(known)
    return {'attempts':len(rows),'paper_full_predictions_all':len(predicted),
            'paper_full_predictions_known':len(known),'unknown_paper_full_outcomes':unknown,
            'actual_full_fills_among_paper_full':full,
            'actual_any_fills_among_paper_full':sum(r.actual_any for r in known),
            'known_only_realization_rate':full/len(known) if known else None,
            'all_prediction_missing_outcome_bounds':[full/len(predicted),(full+unknown)/len(predicted)] if predicted else None}


def _cluster_interval(rows, reps, seed):
    predicted = [r for r in rows if r.paper_full]
    days, markets = defaultdict(list),defaultdict(set)
    for row in predicted:
        days[row.day].append(row); markets[row.market_id].add(row.day)
    result = {'status':'insufficient_clusters','cluster':'whole UTC decision days retaining all market/order dependence',
              'days':len(days),'markets':len(markets),'minimum_days':7,'minimum_markets':20,
              'confidence':.99,'interval':None,'reps':reps,'seed':seed,
              'assumption':'Independent exchangeable days; descriptive, not multiplicity adjusted.'}
    if any(not row.known for row in predicted):
        result['status']='unresolved_outcomes'; return result
    if any(len(ds)>1 for ds in markets.values()):
        result['status']='market_spans_multiple_day_clusters'; return result
    if len(days)<7 or len(markets)<20: return result
    if reps<100: raise ValueError('bootstrap reps must be >=100')
    cluster_values=[(sum(r.actual_full for r in rs),len(rs)) for rs in days.values()]
    rng=random.Random(seed); samples=[]
    for _ in range(reps):
        draws=[rng.choice(cluster_values) for _ in cluster_values]
        samples.append(sum(x for x,n in draws)/sum(n for x,n in draws))
    result.update(status='descriptive_only',interval=[_quantile(samples,.005),_quantile(samples,.995)])
    return result


def _economics(rows):
    result={}
    for source in ('paper','actual'):
        values=[r.pnl(source) for r in rows]
        known=[v for v in values if v is not None]
        result[source+'_economics_complete_rows']=len(known)
        result[source+'_economics_missing_rows']=len(values)-len(known)
        result[source+'_pnl_usd']=sum(known) if values and len(known)==len(values) else None
        result[source+'_observed_rows_pnl_usd']=sum(known) if known else None
    return result


def _adverse_selection(rows):
    sample=[r for r in rows if r.paper_full and r.known and r.pnl('paper') is not None]
    loss=[r for r in sample if r.pnl('paper')<0]; profit=[r for r in sample if r.pnl('paper')>=0]
    return {'paper_economics_classified_known_rows':len(sample),
            'loss_fill_miss_profit_fill_miss':[sum(r.actual_any for r in loss),sum(not r.actual_any for r in loss),sum(r.actual_any for r in profit),sum(not r.actual_any for r in profit)],
            'one_sided_fisher_p_value':None,
            'inference_status':'descriptive_only_no_iid_fisher_or_profitability_claim',
            **_economics(sample)}


def calibrate(attempts: Iterable[Attempt], *, bootstrap_reps=10000, seed=500):
    rows=[parse_attempt(asdict(row)) for row in attempts]
    if len({r.attempt_id for r in rows})!=len(rows): raise ValueError('attempt_id values must be unique')
    observed_match=[r for r in rows if r.match_ms is not None]
    known=[r for r in rows if r.known]
    buckets=defaultdict(list)
    for row in rows:
        lag=row.match_ms-row.decision_ms if row.match_ms is not None else None
        name='unobserved_match_clock' if lag is None else 'lt500ms' if lag<500 else '500_599ms' if lag<600 else '600_999ms' if lag<1000 else 'ge1000ms'
        buckets[name].append(row)
    paper_shares=sum(r.paper_fill_shares for r in known)
    return {'schema':'mine500-private-fak-calibration-v2',
            'supersedes':{'schema':'mine500-private-fak-calibration-v1','status':'invalid_for_evidence'},
            **_rate(rows),'unique_markets':len({r.market_id for r in rows}),
            'utc_decision_days':len({r.day for r in rows}),
            'status_counts':dict(sorted(Counter(r.actual_status for r in rows).items())),
            'known_outcomes':len(known),'unknown_outcomes':len(rows)-len(known),
            'paper_full_realization_cluster_99pct_interval':_cluster_interval(rows,bootstrap_reps,seed),
            'paper_full_realization_exact_99pct_interval':None,
            'paper_false_positive_full_count':sum(r.paper_full and not r.actual_full for r in known),
            'paper_false_negative_any_count':sum(r.actual_any and r.paper_fill_shares<=EPS for r in known),
            'paper_fill_shares_known':paper_shares,'actual_fill_shares_known':sum(r.actual_fill_shares for r in known),
            'share_realization_ratio_known_only':sum(r.actual_fill_shares for r in known)/paper_shares if paper_shares else None,
            'latency_ms':{'observed_match_clock_rows':len(observed_match),'missing_match_clock_rows':len(rows)-len(observed_match),
                'decision_to_match_p50':_quantile([r.match_ms-r.decision_ms for r in observed_match],.5),
                'decision_to_match_p95':_quantile([r.match_ms-r.decision_ms for r in observed_match],.95),
                'send_to_match_p50':_quantile([r.match_ms-r.send_ms for r in observed_match],.5),
                'match_to_response_p50':_quantile([r.response_ms-r.match_ms for r in observed_match],.5),
                'max_reported_clock_error_bound_ms':max((r.match_clock_error_bound_ms for r in observed_match),default=None),
                'venue_hold_is_already_in_match_clock':True,
                'interpretation':'Observed filled-trade match clocks only; no-fill orders remain in overall denominators and unobserved bucket.'},
            'by_total_decision_to_match_bucket':{name:_rate(rs) for name,rs in sorted(buckets.items())},
            'economics':_economics(rows),'adverse_selection':_adverse_selection(rows),
            'qualification':{'strategy_result':False,'public_depth_is_not_actual_fill':True,
                'may_change_frozen_candidate_rules':False,'real_receipt_provenance_verified':False,
                'note':'Input provenance is declared, not independently authenticated; all output remains research calibration.'}}


def main(argv: Sequence[str] | None = None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempts_jsonl'); parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    Path(args.out).write_text(json.dumps(calibrate(load_attempts(args.attempts_jsonl)),indent=2,sort_keys=True,allow_nan=False)+'\n')


if __name__=='__main__':main()
