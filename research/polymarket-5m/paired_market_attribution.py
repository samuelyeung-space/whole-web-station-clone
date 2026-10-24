"""Paired market-level attribution on complete development order ledgers, never OOS inference."""
import argparse
import datetime
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


def aggregate(rows):
    result=defaultdict(dict)
    for r in rows:
        if not r.get('sent'):raise ValueError('complete sent-order ledgers required')
        if not r.get('market_id') or not r.get('observation_run_id'):
            raise ValueError('missing market/run identity')
        stamp=float(r['signal_receive_ms'])
        if not math.isfinite(stamp):raise ValueError('invalid receipt timestamp')
        day=datetime.datetime.fromtimestamp(stamp/1000,datetime.timezone.utc).date().isoformat()
        if r.get('filled') and (r.get('winner') is None or r.get('pnl') is None):
            raise ValueError('unresolved filled position')
        pnl=float(r.get('pnl') or 0)
        if not math.isfinite(pnl):raise ValueError('nonfinite PnL')
        if not r.get('filled') and abs(pnl)>1e-9:raise ValueError('unfilled order has PnL')
        lag=str(int(float(r['evaluation_ms'])))
        m=result[lag].setdefault(r['market_id'],dict(pnl=0.0,sent=0,fills=0,run=r['observation_run_id'],day=day,winner=None))
        if (m['run'],m['day']) != (r['observation_run_id'],day):raise ValueError('market identity conflict')
        if r.get('winner') is not None:
            if m['winner'] is not None and m['winner']!=r['winner']:raise ValueError('winner conflict')
            m['winner']=r['winner']
        m['pnl']+=pnl;m['sent']+=1;m['fills']+=int(bool(r.get('filled')))
    return dict(result)


def compare(baseline_rows,candidate_rows):
    b=aggregate(baseline_rows);c=aggregate(candidate_rows)
    if set(b)!=set(c):raise ValueError('latency coverage differs')
    output={}
    for lag in sorted(b,key=int):
        markets=[]
        identities={(x['run'],x['day']) for arm in (b,c) for x in arm[lag].values()}
        if len({r for r,d in identities})!=1:raise ValueError('recording runs differ')
        for mid in sorted(set(b[lag])|set(c[lag])):
            left=b[lag].get(mid);right=c[lag].get(mid)
            if left and right:
                if (left['run'],left['day'])!=(right['run'],right['day']):raise ValueError('paired identity conflict')
                if left['winner'] is not None and right['winner'] is not None and left['winner']!=right['winner']:raise ValueError('paired outcome conflict')
            markets.append(dict(market_id=mid,baseline_pnl=left['pnl'] if left else 0.0,
                candidate_pnl=right['pnl'] if right else 0.0,
                baseline_sent=left['sent'] if left else 0,candidate_sent=right['sent'] if right else 0,
                baseline_fills=left['fills'] if left else 0,candidate_fills=right['fills'] if right else 0))
        for m in markets:m['delta_pnl']=m['candidate_pnl']-m['baseline_pnl']
        positive=sorted((m['delta_pnl'] for m in markets if m['delta_pnl']>1e-9),reverse=True)
        delta=sum(m['delta_pnl'] for m in markets)
        output[lag]=dict(sent_market_union=len(markets),baseline_pnl=sum(m['baseline_pnl'] for m in markets),
            candidate_pnl=sum(m['candidate_pnl'] for m in markets),delta_pnl=delta,
            positive_delta_markets=len(positive),negative_delta_markets=sum(m['delta_pnl']< -1e-9 for m in markets),
            zero_delta_markets=sum(abs(m['delta_pnl'])<=1e-9 for m in markets),
            best_positive_delta=max(positive,default=0.0),
            delta_without_best_positive_market=delta-sum(positive[:1]),
            delta_without_top3_positive_markets=delta-sum(positive[:3]),
            day_clusters=len({d for r,d in identities}),day_basis='signal local receipt UTC date; exported day may be null for unfilled orders',day_cluster_interval=None,
            markets=markets)
    return dict(schema='paired-market-attribution-v1',by_latency=output,
        cohort='union of complete sent-order ledgers; no claim of full-calendar coverage',
        evidence_class='post_development_descriptive_attribution',
        removal_stress='hindsight deletion of positive paired differences; not executable selection',
        formal_oos_result=None,rank7_pnl_usd=None,confirmed_edge=False)


def run(baseline,candidate):
    inputs=[];rows=[]
    for path in (baseline,candidate):
        p=Path(path);raw=p.read_bytes();inputs.append(dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        rows.append([json.loads(l) for l in raw.splitlines() if l.strip()])
    result=compare(*rows);result['inputs']=dict(zip(('baseline','candidate'),inputs));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',required=True);p.add_argument('--candidate',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=Path(a.out)
    if out.exists():raise SystemExit('refuse to overwrite')
    out.write_text(json.dumps(run(a.baseline,a.candidate),indent=2,sort_keys=True)+'\n')
