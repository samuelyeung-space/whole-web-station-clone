"""Offline, content-addressed research replay. No orders, network or model fitting.

prepare builds reusable causal JPR/MAR feature tapes from normalized events,
receipt-clock trades and separately frozen model scores. run accepts strategy
modules exposing catalog()/candidates() and signal(candidate, features).
This is a replay/cache entrypoint, not a historical raw-data acquisition service.
"""
import argparse,gzip,importlib,json,time
from pathlib import Path
import joint_probability_research as jp
import joint_probability_execution as ex
import mean_regime_research as ma

def strategies(specs):
    candidates=[]; functions={}
    for spec in specs:
        module,cid=spec.split(':',1); plugin=importlib.import_module(module)
        catalog=(getattr(plugin,'catalog',None) or plugin.candidates)()
        matches=[c for c in catalog if c['id']==cid]
        if len(matches)!=1 or cid in functions:raise ValueError('unknown or duplicated candidate '+cid)
        candidates.append(matches[0]);functions[cid]=plugin.signal
    return candidates,functions

def prepare(args):
    started=time.perf_counter();root=Path(args.cache);root.mkdir(parents=True,exist_ok=True)
    hashes={k:jp.sha(v) for k,v in [('endpoint',args.endpoint),('prices',args.prices),('scores',args.scores)]}
    hashes.update(enrichment_code=jp.sha(ma.__file__),execution_code=jp.sha(ex.__file__))
    key=ex.fingerprint(hashes);dest=root/key
    if (dest/'manifest.json').exists():
        manifest=json.loads((dest/'manifest.json').read_text())
        if jp.sha(dest/'events.jsonl.gz')!=manifest['events_sha256']:raise ValueError('cache integrity mismatch')
        print(json.dumps({'cache_hit':True,'cache_path':str(dest),'elapsed_seconds':time.perf_counter()-started}));return
    dest.mkdir(exist_ok=False)
    scores={(r['market_id'],r['recv_ms']):r['scores'] for r in ex.read_jsonl(args.scores)}
    events,coverage=ma.enrich(args.endpoint,args.prices,scores)
    with gzip.open(dest/'events.jsonl.gz','wt',compresslevel=1) as f:
        for event in events:f.write(json.dumps(event,separators=(',',':'),allow_nan=False)+'\n')
    header=events[0]
    manifest={'cache_key':key,'hashes':hashes,'events_sha256':jp.sha(dest/'events.jsonl.gz'),'event_count':len(events),
        'coverage_start_ms':header['coverage_start_ms'],'coverage_end_ms':header['coverage_end_ms'],
        'markets':len({e['market_id'] for e in events if e['type']=='market'}),'feature_coverage':coverage,
        'input_split':header.get('split'),'formal_oos_result':None,'no_model_fitting':True}
    jp.dump(dest/'manifest.json',manifest)
    print(json.dumps({'cache_hit':False,'cache_path':str(dest),'elapsed_seconds':time.perf_counter()-started,**manifest}))

def run(args):
    started=time.perf_counter();cache=Path(args.cache);manifest=json.loads((cache/'manifest.json').read_text())
    if jp.sha(cache/'events.jsonl.gz')!=manifest['events_sha256']:raise ValueError('cache integrity mismatch')
    if jp.sha(ex.__file__)!=manifest['hashes']['execution_code']:raise ValueError('execution code changed: rebuild cache/registration')
    candidates,functions=strategies(args.strategy);events=list(ex.read_jsonl(cache/'events.jsonl.gz'))
    def signal(c,f):return functions[c['id']](c,f)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    registration={'candidate_rules':candidates,'strategy_modules':args.strategy,'cache_key':manifest['cache_key'],
        'cash_scope':'separate $1000 ledger per candidate and latency; do not sum','formal_oos_result':None,
        'purpose':'retrospective/dev replay; data eligibility and model cutoff must be separately documented',
        'plugin_code_sha256':{spec.split(':')[0]:jp.sha(importlib.import_module(spec.split(':')[0]).__file__) for spec in args.strategy}}
    jp.dump(out/'registration.json',registration)
    result=ex.replay_events(events,candidates,signal,ex.ReplayConfig(shares=args.shares,initial_cash_usd=1000,
        extra_cost_per_filled_share_usd=args.extra_cost,depth_fraction=args.depth_fraction,
        full_or_zero=args.full_or_zero,require_constraints=True,latencies_ms=(500,600,1000)))
    with gzip.open(out/'attempts.jsonl.gz','wt') as f:
        for a in result.pop('attempts'):f.write(json.dumps(a)+'\n')
    elapsed=time.perf_counter()-started;result['wall_seconds_including_read_and_write']=elapsed
    jp.dump(out/'audit.json',result)
    print(json.dumps({'elapsed_seconds':elapsed,'scenario_count':len(result['summary']),'summary':result['summary']}))

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare')
    for arg in ['endpoint','prices','scores','cache']:p.add_argument('--'+arg,required=True)
    r=sub.add_parser('run');r.add_argument('--cache',required=True);r.add_argument('--out',required=True)
    r.add_argument('--strategy',action='append',required=True,help='module:candidate-id; repeatable')
    r.add_argument('--shares',type=float,default=5);r.add_argument('--extra-cost',type=float,default=0)
    r.add_argument('--depth-fraction',type=float,default=1);r.add_argument('--full-or-zero',action='store_true')
    args=ap.parse_args();(prepare if args.command=='prepare' else run)(args)
if __name__=='__main__':main()
