"""Fixed arbitration of previously selected rules on consumed Oct9 data only.

One 100-share order per market, earliest eligible signal; simultaneous priority
JPR-035, MAR-089, MAR-099. Failed first orders never retry. No rule retuning.
This tests a shared cash ledger, not maker fills or a two-leg hedge.
"""
import gzip
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joint_probability_execution as ex
import joint_probability_research as jp
import mean_regime_research as ma


def selected_rules():
    return [(next(c for c in jp.catalog() if c['id']=='JPR-035'), jp.signal)] + [
        (next(c for c in ma.candidates() if c['id']==cid), ma.signal)
        for cid in ['MAR-089','MAR-099']]


def eligible(candidate, features, available, now):
    names=set(candidate['required_features']) | {'tau_s'}
    return all(ex._number(features.get(n)) and ex._number(available.get(n))
               and available[n] <= now for n in names)


def arbitrate(events):
    rules=selected_rules(); first={}; choices={}; logs=[]; invalid=Counter()
    for e in events:
        if e['type']!='decision':
            continue
        now=e['recv_ms']; market=e['market_id']; f=e['features']; av=e['feature_available_ms']
        hits=[]
        for c,fn in rules:
            if not eligible(c,f,av,now):
                invalid[c['id']]+=1
                continue
            side=fn(c,{n:f[n] for n in set(c['required_features'])|{'tau_s'}})
            if side:
                hits.append((c['id'],side))
                first.setdefault((c['id'],market),{'candidate_id':c['id'],'market_id':market,'decision_ms':now,'side':side})
        # A union with per-rule validation: missing MA columns cannot block JPR.
        f['portfolio_side']=0; av['portfolio_side']=now
        if hits and market not in choices:
            cid,side=hits[0]; f['portfolio_side']=1 if side=='Up' else -1
            choice={'market_id':market,'decision_ms':now,'selected_candidate':cid,'side':side,
                    'simultaneous_signals':hits,'opposite_simultaneous':len({s for _,s in hits})>1}
            choices[market]=choice; logs.append(choice)
    return events,logs,list(first.values()),dict(invalid)


def portfolio_signal(candidate, features):
    return {1:'Up',-1:'Down'}.get(features['portfolio_side'])


def run(path,prices,scorepath,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    rules=selected_rules()
    jp.dump(out/'registration.json',{
        'registered_at_utc':datetime.now(timezone.utc).isoformat(),'split':'consumed_development',
        'selection':'Prior-run development winners, selected before portfolio replay; not OOS',
        'rules':[c for c,_ in rules],'priority':[c['id'] for c,_ in rules],
        'policy':'earliest eligible signal; fixed priority on ties; one order per market; no retry after failure',
        'shares':100,'initial_cash_usd':1000,'latencies_ms':[500,600,1000],
        'cash_release_delay_ms':[0,60000,300000],
        'cash_release_assumption':'after official outcome local knowledge; additional delay is stress, not measured redemption',
        'stresses':['full100_plus1c','partial100_plus1c','halfdepth100_plus1c'],
        'input_sha256':{p:jp.sha(p) for p in [path,prices,scorepath]},
        'code_sha256':{str(Path(p).name):jp.sha(p) for p in [__file__,ex.__file__,jp.__file__,ma.__file__]},
        'formal_oos_result':None,'confirmed_strategies':0})
    scores={}
    for r in ex.read_jsonl(scorepath):scores[(r['market_id'],r['recv_ms'])]=r['scores']
    events,coverage=ma.enrich(path,prices,scores)
    events,choices,first,invalid=arbitrate(events)
    jp.dump(out/'signals.json',{'choices':choices,'component_first_signals':first,
                              'invalid_rows_by_component':invalid,'feature_coverage':coverage})
    byid={c['id']:{r['market_id']:r for r in first if r['candidate_id']==c['id']} for c,_ in rules}
    overlap=[]
    ids=list(byid)
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            shared=set(byid[a])&set(byid[b]);union=set(byid[a])|set(byid[b])
            overlap.append({'a':a,'b':b,'shared_markets':len(shared),'union_markets':len(union),
                            'jaccard':len(shared)/len(union) if union else None,
                            'opposite_first_signal_markets':sum(byid[a][m]['side']!=byid[b][m]['side'] for m in shared)})
    metrics=[]
    for name,depth,full in [('full100_plus1c',1,True),('partial100_plus1c',1,False),('halfdepth100_plus1c',.5,False)]:
        for delay in [0,60000,300000]:
            config=ex.ReplayConfig(shares=100,initial_cash_usd=1000,depth_fraction=depth,
                full_or_zero=full,extra_cost_per_filled_share_usd=.01,
                require_constraints=True,cash_release_delay_ms=delay)
            result=ex.replay_events(events,[{'id':'PORT-001','required_features':['portfolio_side']}],portfolio_signal,config)
            attempts=result.pop('attempts'); selected={r['market_id']:r for r in choices}
            for a in attempts:a['selected_candidate']=selected[a['market_id']]['selected_candidate']
            stem=f'{name}_release{delay}'
            with gzip.open(out/(stem+'_attempts.jsonl.gz'),'wt') as f:
                for a in attempts:f.write(json.dumps(a)+'\n')
            result['capital_scope']='one shared $1000 portfolio per latency; latency scenarios not additive'
            jp.dump(out/(stem+'_audit.json'),result)
            metrics.extend({'scenario':name,**r} for r in result['summary'])
            print(stem,'complete',flush=True)
    jp.dump(out/'metrics.json',metrics)
    jp.dump(out/'completion.json',{'portfolio_rules':1,'selected_component_rules':3,
        'new_execution_scenarios':len(metrics),'scenario_attempt_rows':sum(r['attempts'] for r in metrics),
        'unique_signal_markets':len(choices),'component_first_signal_counts':{k:len(v) for k,v in byid.items()},
        'chosen_rule_counts':dict(Counter(r['selected_candidate'] for r in choices)),
        'pair_overlap':overlap,'independent_days':1,'confirmed_strategies':0,'formal_oos_result':None,
        'limitations':['post-selection development; no new holdout','displayed depth is an upper bound',
                      'cash-release delays assumed','drawdown realized only; no MTM',
                      'not maker or hedging portfolio; conflicting later signals ignored']})


if __name__=='__main__':run(*sys.argv[1:])
