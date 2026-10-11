"""Engine-free verification of recycling event tapes against source potentials."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path


def read(p):
    with gzip.open(p,'rt') as f:return [json.loads(l) for l in f]


def check(a,b):
    if abs(a-b)>1e-6:raise AssertionError((a,b))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--development',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True)
    a=ap.parse_args()
    registration=json.loads((a.results/'registration.json').read_text())
    sources={}
    for s,h in registration['input_hashes'].items():
        p=a.development/(s+'_attempts.jsonl.gz')
        assert hashlib.sha256(p.read_bytes()).hexdigest()==h
        sources[s]={(r['latency_ms'],r['market_id']):r for r in read(p)}
    event_groups,decision_groups=defaultdict(list),defaultdict(list)
    for name,group in [('cash_events',event_groups),('decisions',decision_groups)]:
        for r in read(a.results/(name+'.jsonl.gz')):
            group[(r['scenario'],r['latency_ms'],r['delay_seconds'])].append(r)
    audited=[]
    metrics=json.loads((a.results/'metrics.json').read_text())
    for m in metrics:
        key=(m['scenario'],m['latency_ms'],m['delay_seconds'])
        decisions=decision_groups[key]
        source=sources[key[0]]
        source_rows=[r for (lag,_),r in source.items() if lag==key[1]]
        assert {r['market_id'] for r in decisions}=={r['market_id'] for r in source_rows}
        assert len(decisions)==len(source_rows)
        dmap={r['market_id']:r for r in decisions}
        expected=[]
        # Build the complete event set independently from the admission log.
        for r in source_rows:
            mid=r['market_id'];d=dmap[mid]
            expected.append((r['decision_ms'],0,mid,'decision'))
            if d['admitted']:
                expected.append((r['match_ms'],1,mid,'match'))
                if r['filled_shares']>0:
                    expected.append((r['settlement_ms'],2,mid,'information'))
                    if r['payout_usd']:
                        expected.append((r['settlement_ms']+key[2]*1000,3,mid,'spendable'))
        events=event_groups[key]
        observed=[(e['ts_ms'],{'decision':0,'match':1,'information':2,'spendable':3}[e['event']],e['market_id'],e['event']) for e in events]
        assert sorted(expected)==sorted(observed)
        assert [(e[0],e[1]) for e in observed]==sorted((e[0],e[1]) for e in observed)
        cash=1000.;reserved=open_cost=claims=pnl=fees=shares=0.
        minimum=1000.;peak=1000.;dd=0.;snapshot=None
        fills=rejects=0
        for e in events:
            if snapshot is None and e['ts_ms']>m['last_decision_ms']:
                snapshot=(cash,reserved,open_cost,claims,pnl)
            r=source[(key[1],e['market_id'])];kind=e['event']
            if kind=='decision':
                d=dmap[e['market_id']]
                check(d['available_before_usd'],cash-reserved)
                assert d['admitted']==(cash-reserved+1e-9>=r['reserved_usd_at_decision'])
                if d['admitted']:reserved+=r['reserved_usd_at_decision']
                else:rejects+=1
            elif kind=='match':
                reserved-=r['reserved_usd_at_decision'];cash-=r['cost_usd']
                fees+=r['fees_usd'];shares+=r['filled_shares']
                if r['filled_shares']>0:open_cost+=r['cost_usd'];fills+=1
            elif kind=='information':
                open_cost-=r['cost_usd'];claims+=r['payout_usd']
                pnl+=r['payout_usd']-r['cost_usd'];peak=max(peak,1000+pnl);dd=max(dd,peak-1000-pnl)
            else:cash+=r['payout_usd'];claims-=r['payout_usd']
            values=(cash,reserved,open_cost,claims,pnl)
            for field,value in zip(('cash_usd','reserved_usd','unsettled_cost_usd','payout_receivable_usd','realized_pnl_usd'),values):check(e[field],value)
            assert min(cash-reserved,reserved,open_cost,claims)>=-1e-6
            check(cash+open_cost+claims,1000+pnl)
            minimum=min(minimum,cash-reserved)
        if snapshot is None:snapshot=(cash,reserved,open_cost,claims,pnl)
        for field,value in zip(('cash_usd','reserved_usd','unsettled_cost_usd','payout_receivable_usd','realized_pnl_usd'),snapshot):check(m['last_decision_snapshot'][field],value)
        for field,value in [('net_pnl_usd',pnl),('fees_usd',fees),('filled_shares',shares),('minimum_available_cash_usd',minimum),('max_outcome_realized_drawdown_usd',dd),('terminal_cash_after_assumed_redemptions_usd',cash)]:check(m[field],value)
        assert fills==m['counts']['filled_orders'] and rejects==m['counts'].get('rejected_insufficient_cash',0)
        check(reserved+open_cost+claims,0)
        check(cash,1000+pnl)
        audited.append(dict(scenario=key[0],latency_ms=key[1],delay_seconds=key[2],decisions=len(decisions),events=len(events),discrepancies=0))
    report=dict(alternatives=len(audited),decision_rows=sum(x['decisions'] for x in audited),
        cash_event_rows=sum(x['events'] for x in audited),discrepancies=0,scenarios=audited,
        verification_scope='cash admission, event set, every balance, end-horizon claims and terminal conservation; not actual execution or redemption',
        formal_oos_result=None)
    (a.results/'independent_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='scenarios'}))


if __name__=='__main__':main()
