"""Audit a new append of legacy momentum rows without rerunning a strategy.

Reported per-share results are not strict FAK/cash execution. Unknown labels
remain unknown; corresponding payout bounds are kept separate from the report.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path


def read_csv(path):
    with Path(path).open() as f: return list(csv.DictReader(f))


def label(m):
    p=list(map(float,json.loads(m['outcomePrices'])))
    if m.get('closed') is True and p in ([1.,0.],[0.,1.]):
        return json.loads(m['outcomes'])[p.index(1.)]
    return None


def audit(delta, markets, native_paper, published_paper):
    rows=read_csv(delta); snapshots=defaultdict(list)
    with gzip.open(markets,'rt') as f:
        for line in f:
            m=json.loads(line)
            if m.get('slug','').startswith('btc-updown-5m-'): snapshots[m['slug']].append(m)
    latest={k:max(v,key=lambda m:m['_recorded_at_ms']) for k,v in snapshots.items()}
    official={k:label(v) for k,v in latest.items()}
    seen=set(); groups=defaultdict(list); unknown=set(); per_row=[]
    for r in rows:
        key=(r['market_id'],r['lag'])
        if key in seen: raise ValueError('duplicate market/lag in new append')
        seen.add(key);groups[r['lag']].append(r)
        if r['market_id'] not in snapshots: raise ValueError('unknown capture market')
        w=official[r['market_id']]
        if w is None: unknown.add(r['market_id'])
        elif float(r['won']) != (r['side']==w): raise ValueError('official Gamma label disagrees')
        if r['filled'].lower()=='true':
            p,fee,pnl,size=(Decimal(r[k]) for k in ('price','fee','pnl','size'))
            if not all(x.is_finite() for x in [p,fee,pnl,size]) or size<5: raise ValueError('invalid displayed capacity')
            prior=[m for m in snapshots[r['market_id']] if m['_recorded_at_ms']<=1000*float(r['t'])]
            if not prior: raise ValueError('no predecision metadata')
            m=max(prior,key=lambda m:m['_recorded_at_ms']);schedule=m['feeSchedule']
            if schedule['exponent']!=1: raise ValueError('unsupported fee exponent')
            expected=Decimal(str(schedule['rate']))*p*(1-p)
            if abs(expected-fee)>Decimal('1e-12'): raise ValueError('fee arithmetic mismatch')
            if abs(Decimal(r['won'])-p-fee-pnl)>Decimal('1e-12'): raise ValueError('per-share PnL mismatch')
            per_row.append(dict(market_id=r['market_id'],lag=r['lag'],official_label=w,
                                reported_per_share_pnl=str(pnl),fee_rate=schedule['rate'],
                                fixed_five_share_proxy_usd=str(5*pnl),not_actual_size=r['size']))
    result={}
    for lag, a in sorted(groups.items(),key=lambda x:float(x[0])):
        f=[r for r in a if r['filled'].lower()=='true']
        total=sum((Decimal(r['pnl'])*5 for r in f),Decimal(0))
        known=sum((Decimal(r['pnl'])*5 for r in f if official[r['market_id']] is not None),Decimal(0))
        pending=[r for r in f if official[r['market_id']] is None]
        low=known+sum((-5*(Decimal(r['price'])+Decimal(r['fee'])) for r in pending),Decimal(0))
        high=known+sum((5*(1-Decimal(r['price'])-Decimal(r['fee'])) for r in pending),Decimal(0))
        result[lag]=dict(signal_markets=len(a),proxy_full_five_share_fills=len(f),
            reason_counts=dict(Counter(r['reason'] for r in a)),reported_proxy_pnl_usd=float(total),
            reported_ev_per_filled_share=float(total/(5*len(f))) if f else None,
            independently_labeled_fills=len(f)-len(pending),uncorroborated_label_fills=len(pending),
            partial_label_pnl_bounds_usd=[float(low),float(high)],
            official_complete_pnl_usd=None if pending else float(total),
            day_cluster_interval=None,strict_100share_cash_pnl_usd=None)
    native=read_csv(native_paper); published=read_csv(published_paper)
    identity=lambda r:(r['slug'],int(r['tau']),int(r['decided_ms']))
    by=defaultdict(list)
    for r in published: by[identity(r)].append(r)
    for r in native:
        candidates=by[identity(r)]
        if len(candidates)!=1: raise ValueError('native paper publication missing or ambiguous')
        q=candidates[0]
        for k in ('side','won'):
            if q[k]!=r[k]: raise ValueError('paper identity changed')
        for k in ('ask','shares','fee','cost','pnl'):
            if abs(float(q[k])-float(r[k]))>1e-8: raise ValueError('paper amount changed')
    market_counts={float(lag):len(a) for lag,a in groups.items()}
    if len(set(market_counts.values()))!=1: raise ValueError('lag populations differ')
    digests={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [delta,markets,native_paper,published_paper]}
    return dict(schema='published-momentum-increment-audit-v1',classification='legacy descriptive proxy arithmetic only',
        appended_rows=len(rows),unique_signal_markets=len({r['market_id'] for r in rows}),
        lags_s=sorted(map(float,groups)),summaries=result,uncorroborated_label_markets=sorted(unknown),
        per_filled_row=per_row,native_paper_publication=dict(reconciled_rows=len(native),
            exact_existing_paper_records=True,new_independent_paper_records=0),input_sha256=digests,
        signal_clock='completed-second Binance local receipt grid',
        quote_clock='legacy Book indexes CLOB source_ts, not recv; Down ask is1-Upbid',
        fixed_decision_limit_and_fak_verified=False,strict_cash_replay_performed=False,
        actual_fills=0,complete_utc_days=0,new_strategy_attempts=0,formal_oos_result=None,rank7_pnl_usd=None)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('delta');p.add_argument('markets');p.add_argument('native_paper');p.add_argument('published_paper');p.add_argument('out')
    a=p.parse_args();r=audit(a.delta,a.markets,a.native_paper,a.published_paper)
    Path(a.out).write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:r[k] for k in ['appended_rows','unique_signal_markets','summaries','native_paper_publication']}))
