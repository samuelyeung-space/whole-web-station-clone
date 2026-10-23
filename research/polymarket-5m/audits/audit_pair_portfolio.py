"""Engine-free checks of shared portfolio intents, books, fees and cash.

Uses frozen component formulas independently of the arbitration implementation.
Original mutation traces must be intact. No financial replay or live orders.
"""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
from pathlib import Path

from cash_release_audit import reconcile, close


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def rows(path):
    with gzip.open(path,'rt') as f:
        for line in f:yield json.loads(line)


def finite(x):return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x)


def audit_decision_cash(attempts, events, config):
    by=defaultdict(list);mutation={}
    for e in events:
        by[(e['candidate_id'],e['latency_ms'])].append(e)
        mutation[(e['attempt_id'],e['event_type'])]=e
    times={k:[r['event_ms'] for r in v] for k,v in by.items()}
    rejected=0
    for r in attempts:
        k=(r['candidate_id'],r['latency_ms']);aid=r['attempt_id']
        if r['submitted']:
            e=mutation[(aid,'reservation')]
            available_before=e['cash_usd']-e['reserved_usd']+r['reserved_usd_at_decision']
            cash_before=e['cash_usd']
        else:
            tt=times.get(k,[]);i=bisect_left(tt,r['decision_ms'])
            if i<len(tt) and tt[i]==r['decision_ms']:
                raise ValueError('unsubmitted decision has ambiguous coincident account mutation')
            e=by[k][i-1] if i else dict(cash_usd=1000.,reserved_usd=0.)
            available_before=e['cash_usd']-e['reserved_usd'];cash_before=e['cash_usd']
        close(r['cash_before_decision'],cash_before,'observed decision cash')
        if r['submitted'] or r['status']=='rejected_insufficient_available_cash':
            price=Decimal(str(r['limit_price']));rate=Decimal(str(r['fee_rate_decision']))
            fee=(Decimal(100)*rate*price*(1-price)).quantize(Decimal('.00001'),rounding=ROUND_CEILING)
            need=100*r['limit_price']+float(fee)+100*config['extra_cost_per_filled_share_usd']
            if r['submitted']:
                close(r['reserved_usd_at_decision'],need,'fixed100 reservation')
                if available_before+1e-9<need:raise ValueError('unfunded reservation')
            else:
                if available_before+1e-9>=need:raise ValueError('cash rejection despite sufficient funds')
                rejected+=1
        if r['cash_after_match'] is not None:
            e=mutation[(aid,'fill' if r['filled_shares'] else 'no_fill')]
            close(r['cash_after_match'],e['cash_usd'],'saved match cash')
        if r['settled']:
            e=mutation[(aid,'settlement')]
            close(r['cash_after_settlement'],e['cash_usd'],'saved outcome cash')
            close(r['realized_equity_after_settlement'],1000+e['realized_pnl_usd'],'saved equity')
    return dict(all_decision_balances_verified=len(attempts),cash_rejections_proved_insufficient=rejected)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--results',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();root=a.results;reg=json.loads((root/'registration.json').read_text())
    done=json.loads((root/'completion.json').read_text())
    if sha(a.input)!=reg['input_sha256']:raise ValueError('raw normalized input changed')
    for name,record in done['outputs'].items():
        f=root/name
        if sha(f)!=record['sha256'] or f.stat().st_size!=record['bytes']:raise ValueError('saved artifact changed')
        if f.suffix=='.gz' and sum(1 for _ in rows(f))!=record['gzip_rows']:raise ValueError('gzip rows changed')
    books=defaultdict(list);clocks=defaultdict(list);outcomes={};market={};first={}
    cs=reg['components']
    for e in rows(a.input):
        mid=e.get('market_id')
        if e['type']=='market':market[mid]=e
        elif e['type']=='book':books[mid].append(e);clocks[mid].append(e['recv_ms'])
        elif e['type']=='outcome':outcomes[mid]=e
        elif e['type']=='decision':
            now=e['recv_ms'];f=e['features'];av=e['feature_available_ms'];m=market.get(mid)
            if m is None or not m['start_ms']<=now<m['end_ms']:continue
            for c in cs:
                needed=set(c['required_features'])|{'tau_s'};params=c['params']
                if not all(finite(f.get(n)) and finite(av.get(n)) and av[n]<=now for n in needed):continue
                tau=f['tau_s'];hi=params['tau_max_s']
                if abs(tau-(m['end_ms']-now)/1000)>.001:continue
                if not params['tau_min_s']<=tau or not (tau<=hi if params['tau_upper_inclusive'] else tau<hi):continue
                if c['id']=='M500-010':score=f['r_5s']
                elif c['id']=='M500-680':
                    if not -1<=f['pm_r_5s']<=1 or not -1<=f['pm_r_15s']<=1:continue
                    score=-(f['pm_r_5s']-(f['pm_r_15s']-f['pm_r_5s'])/2)
                else:raise ValueError('unregistered component formula')
                if abs(score)<params['threshold'] or score==0:continue
                item=(now,c['id'],'Up' if score>0 else 'Down')
                if mid not in first or item<first[mid]:first[mid]=item
    signals=json.loads((root/'signals.json').read_text())
    if {r['market_id']:(r['decision_ms'],r['selected_candidate'],r['side']) for r in signals['choices']}!=first:
        raise ValueError('independently computed earliest rule differs')
    report=dict(schema='shared-portfolio-independent-audit-v1',new_financial_replays=0,
        independent_arbitration_markets=len(first),rank7_result=None,formal_oos_result=None,scenarios={})
    for name,config in reg['configs'].items():
        folder=root/name;aa=list(rows(folder/'attempts.jsonl.gz'));ee=list(rows(folder/'cash_events.jsonl.gz'))
        doc=json.loads((folder/'summary.json').read_text());metrics=doc['summary']
        if doc['config']!=config:raise ValueError('configuration changed')
        if len({(r['latency_ms'],r['market_id']) for r in aa})!=len(aa):raise ValueError('market retried')
        for r in aa:
            if r['requested_shares']!=100 or r['latency_ms'] not in (500,600,1000):
                raise ValueError('wrong size or total latency')
            expected=first[r['market_id']]
            if (r['decision_ms'],r['side'])!=(expected[0],expected[2]):raise ValueError('first signal or side changed')
            close(r['match_ms']-r['decision_ms'],r['latency_ms'],'actual local delay')
            if not r['filled_shares']:
                close(r['cost_usd'],0.,'unfilled cash debit');continue
            if not r['submitted'] or not r['settled']:raise ValueError('invalid filled lifecycle')
            i=bisect_right(clocks[r['market_id']],r['match_ms'])-1
            if i<0:raise ValueError('no book available at match')
            b=books[r['market_id']][i]
            close(b['recv_ms'],r['book_recv_ms_at_match'],'latest local book')
            if not b['healthy']:raise ValueError('unhealthy fill')
            if any(not 0<=r['match_ms']-b[s+'_recv_ms']<=250 for s in ('up','down')):raise ValueError('stale token')
            j=bisect_right(clocks[r['market_id']],r['decision_ms'])-1;db=books[r['market_id']][j]
            close(r['limit_price'],min(p for p,q in db[r['side'].lower()+'_asks'] if q>0),'fixed first ask limit')
            levels=defaultdict(float)
            for price,depth in b[r['side'].lower()+'_asks']:levels[price]+=depth
            left=100.;gross=0.;fee=Decimal(0)
            for price,depth in sorted(levels.items()):
                if price>r['limit_price']+1e-9 or left<=1e-9:break
                take=min(left,depth*config['depth_fraction']);left-=take;gross+=take*price
                fee+=Decimal(str(take))*Decimal(str(price))*Decimal(str(b['fee_rate']))*(1-Decimal(str(price)))
            qty=100-left;rounded=Decimal(0) if fee<Decimal('.00001') else fee.quantize(Decimal('.00001'),rounding=ROUND_HALF_UP)
            close(qty,r['filled_shares'],'eligible FAK quantity');close(gross/qty,r['fill_vwap'],'VWAP')
            close(r['fees_usd'],float(rounded),'fee precision proxy')
            close(r['cost_usd'],gross+float(rounded)+qty*config['extra_cost_per_filled_share_usd'],'net debit')
            o=outcomes[r['market_id']]
            close(r['settlement_ms'],max(o['recv_ms'],o['resolved_ms']),'official outcome knowledge')
            if r['winner']!=o['winner']:raise ValueError('official label changed')
            close(r['payout_usd'],qty if r['side']==o['winner'] else 0.,'payoff')
            for phase,now in [('decision',r['decision_ms']),('match',r['match_ms'])]:
                c=r['constraints_at_'+phase]
                if not c['validated'] or any(v['available_ms']>now for v in c['provenance'].values()):
                    raise ValueError('unavailable execution constraint')
        cash=reconcile(aa,ee,metrics,config['cash_release_delay_ms'])
        decisions=audit_decision_cash(aa,ee,config)
        for m in metrics:
            rr=[r for r in aa if r['latency_ms']==m['latency_ms']]
            if Counter(r['status'] for r in rr)!=Counter(m['status_counts']):raise ValueError('status counts mismatch')
            for field,value in [('attempts',len(rr)),('submitted_orders',sum(r['submitted'] for r in rr)),
                 ('full_fills',sum(r['status']=='filled_full' for r in rr)),
                 ('partial_fills',sum(r['status']=='filled_partial' for r in rr))]:
                if m[field]!=value:raise ValueError('summary lifecycle count mismatch')
            for field in ('fees_usd','filled_shares'):close(m[field],math.fsum(r[field] for r in rr),'summary '+field)
            close(m['net_pnl_usd'],math.fsum(r['net_pnl_usd'] for r in rr if r['settled']),'summary profit')
            peak=1000.;drawdown=0.
            for e in ee:
                if e['latency_ms']==m['latency_ms'] and e['event_type']=='settlement':
                    equity=1000+e['realized_pnl_usd'];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
            close(m['max_realized_drawdown_usd'],drawdown,'realized drawdown')
        report['scenarios'][name]=dict(accounts=len(metrics),attempts=len(aa),cash_events=len(ee),
            filled_orders=sum(r['filled_shares']>0 for r in aa),cash_audit=cash,decision_audit=decisions,
            independently_checked_execution_and_earliest_rule=True)
    with a.out.open('x') as f:json.dump(report,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({n:{k:v for k,v in r.items() if k not in ('cash_audit','decision_audit')} for n,r in report['scenarios'].items()}))


if __name__=='__main__':main()
