"""Independent historical100 artifact completeness and signal-survivorship gate.
Checks recorded claims only: no raw-book authentication, replay, or OOS promotion.
"""
from collections import Counter
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

GRID = {(r,l) for r in ('R1','R2','R3') for l in (500,600,1000)}
HOLD = {'R1':None,'R2':60000,'R3':30000}

def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def rows(p):
    with gzip.open(p,'rt') as f: return [json.loads(x) for x in f]

def same(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isfinite(a) and math.isfinite(b) and abs(a-b)<=1e-6
    return a==b

def audit_attempts(signals,attempts,exits,markets,rule,lag,cfg):
    errors=[]
    def err(code,**kw): errors.append(dict(code=code,**kw))
    selected=[s for s in signals if s['rule_id']==rule]
    wanted=Counter(s['signal_id'] for s in selected); got=Counter(a['attempt_id'] for a in attempts)
    if any(n!=1 for n in wanted.values()): err('duplicate_signal_identity')
    if wanted!=got: err('signal_population_changed',missing=dict(wanted-got),extra=dict(got-wanted))
    sm={s['signal_id']:s for s in selected}; am={a['attempt_id']:a for a in attempts}
    for a in attempts:
        aid=a['attempt_id']; s=sm.get(aid)
        if s is None: continue
        for k in ('market_id','rule_id','side','decision_ms'):
            if not same(a.get(k),s.get(k)): err('signal_mutated',id=aid,field=k)
        if not same(a.get('requested_shares'),100): err('order_size_changed',id=aid)
        if not same(a.get('latency_ms'),lag) or not same(a.get('match_ms'),s['decision_ms']+lag): err('total_latency_changed',id=aid)
        if a.get('limit_price') is not None and not same(a['limit_price'],s.get('asklimit')): err('fixed_limit_changed',id=aid)
        for k,t in [('decision_book_recv_ms','decision_ms'),('match_book_recv_ms','match_ms')]:
            if k in a and not 0<=a[t]-a[k]<=cfg['max_age_ms']: err('noncausal_or_stale_book',id=aid,field=k)
        m=markets.get(str(a['market_id']))
        if m is None: err('missing_market',id=aid); continue
        if a['filled_shares']>1e-8 and max(a['decision_ms'],a['match_ms'])>=m['end_ms']: err('entry_after_end',id=aid)
        q=a.get('settlement_shares',0)
        if q>1e-8:
            outcome=m.get('outcome')
            if outcome not in ('up','down'): err('settlement_without_outcome',id=aid)
            elif not same(a.get('payout_usd'),q if a['side']==outcome else 0): err('payout_disagrees_with_market',id=aid)
            known=m.get('outcome_known_ms')
            release=known if known is not None else m['end_ms']+cfg['imputed_outcome_delay_ms']
            if not same(a.get('settlement_ms'),release): err('cash_release_clock_changed',id=aid)
    for x in exits:
        a=am.get(x['attempt_id'])
        if a is None: err('exit_without_attempt'); continue
        hold=HOLD[rule]
        if hold is None or x['decision_ms']<a['match_ms']+hold: err('exit_before_holding_period',id=x['attempt_id'])
        if x.get('match_ms') is not None and not same(x['match_ms'],x['decision_ms']+lag): err('exit_latency_changed',id=x['attempt_id'])
        for k,t in [('decision_book_recv_ms','decision_ms'),('match_book_recv_ms','match_ms')]:
            if k in x and not 0<=x[t]-x[k]<=cfg['max_age_ms']: err('noncausal_or_stale_exit_book',id=x['attempt_id'])
    return errors

def audit(root):
    root=Path(root); errors=[]
    def err(code,**kw): errors.append(dict(code=code,**kw))
    def read(n): return json.loads((root/n).read_text())
    required={'registration.json','coverage.json','signals.jsonl.gz','markets.json','metrics.json','run_status.json','source_MANIFEST.txt','kline_provenance.json'}
    for r,l in GRID:
        stem=f'{r}_{l}ms'; required.add(stem+'_summary.json')
        required.update(stem+'_'+k+'.jsonl.gz' for k in ('attempts','exits','cash_events'))
    missing=sorted(n for n in required|{'MANIFEST.json'} if not (root/n).is_file())
    if missing: return dict(passed=False,errors=[dict(code='incomplete_result_files',missing=missing)],result=None)
    manifest=read('MANIFEST.json')
    for n in sorted(required):
        e=manifest.get(n,{})
        if e.get('sha256')!=sha(root/n) or e.get('bytes')!=(root/n).stat().st_size: err('artifact_integrity_failure',file=n)
    if errors: return dict(passed=False,errors=errors,result=None)
    reg,cov,status=read('registration.json'),read('coverage.json'),read('run_status.json')
    if reg.get('segments')!=['B','C'] or reg.get('max_days') is not None: err('not_full_BC_scope')
    if (reg.get('shares'),reg.get('initial_cash'),reg.get('latencies_ms'),reg.get('rules'))!=(100,1000,[500,600,1000],['R1','R2','R3']): err('registered_grid_changed')
    if reg.get('manifest_sha256')!=sha(root/'source_MANIFEST.txt'): err('source_manifest_changed')
    arcs={a['name']:a for a in reg['archives']}; days=cov.get('days',[])
    if len(arcs)!=21 or len(reg['archives'])!=21: err('registered_archive_count_changed')
    if cov.get('complete') is not True or cov.get('failures') or len(days)!=21: err('incomplete_source_coverage')
    if Counter(d['archive'] for d in days)!=Counter(arcs.keys()): err('archive_population_changed')
    for d in days:
        a=arcs.get(d['archive'],{})
        if d.get('status')!='verified_parsed_scored' or any(d.get(k)!=a.get(k) for k in ('sha256','bytes')): err('archive_not_verified',archive=d['archive'])
    if status.get('status')!='historical_capacity_replay_complete' or status.get('alternatives')!=9: err('replay_incomplete')
    metrics=read('metrics.json')
    if Counter((m.get('rule',m.get('rule_id')),m['latency_ms']) for m in metrics)!=Counter(GRID): err('scenario_population_changed')
    signals,markets=rows(root/'signals.jsonl.gz'),read('markets.json')
    if cov.get('unique_signals')!=len(signals): err('signal_count_mismatch')
    if any(s.get('rule_id') not in HOLD for s in signals): err('unexpected_signal_rule')
    scenarios=[]
    for r,l in sorted(GRID):
        stem=f'{r}_{l}ms'; cfg=read(stem+'_summary.json')['config']
        if (cfg.get('rule_id'),cfg.get('latency_ms'),cfg.get('initial_cash_usd'),cfg.get('requested_shares'),cfg.get('total_latency_includes_venue_wait'))!=(r,l,1000,100,True): err('scenario_configuration_changed',scenario=stem)
        attempts=rows(root/(stem+'_attempts.jsonl.gz')); exits=rows(root/(stem+'_exits.jsonl.gz'))
        found=audit_attempts(signals,attempts,exits,markets,r,l,cfg)
        errors.extend(dict(scenario=stem,**e) for e in found)
        scenarios.append(dict(scenario=stem,selected=sum(s['rule_id']==r for s in signals),attempts=len(attempts),errors=len(found)))
    return dict(format='historical100-evidence-gate-v1',passed=not errors,errors=errors,scenarios=scenarios,
        archive_count=len(days),qualified_strategies=0,formal_new_oos_result=None,
        limitations=['Checks recorded claims, not raw-book authenticity.','Archive accepted labels not independently fetched official outcomes.','Historical fee/fill/redemption assumptions remain unverified.'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists(): raise FileExistsError('Do not overwrite earlier audit')
    result=audit(a.input);a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'passed':result['passed'],'discrepancies':len(result['errors'])}))
    raise SystemExit(0 if result['passed'] else 2)
