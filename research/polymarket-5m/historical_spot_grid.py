"""Reuse cached normalized tape for the original 720 registered rules.

Predeclare three separate sensitivity scenarios. Existing first-500 baseline
results are reused, not rerun or counted twice; supplementary rules stay intact.
"""
import argparse,gzip,json,time
from pathlib import Path
import mine500_catalog as catalog
import mine500_replay_v2 as replay
from historical_spot_development import sha

SCENARIOS=[('full_depth',1,0),('half_depth',.5,0),('plus_1cent',1,.01)]

def registration(out):
    cs=catalog.make_candidates()+catalog.make_supplementary_candidates()
    r={'purpose':'predeclared retrospective development grid; no tuning',
       'candidates':cs,'scenarios':SCENARIOS,'latencies_ms':[500,600,1000],'shares':5,
       'cash_scope':'$1000 independently per rule/scenario/latency; do not sum',
       'catalog_sha256':sha(catalog.__file__),'replay_sha256':sha(replay.__file__),
       'formal_oos_result':None}
    p=Path(out);p.mkdir(parents=True,exist_ok=False)
    (p/'registration.json').write_text(json.dumps(r,indent=2)+'\n')

def run(source,out):
    source,out=Path(source),Path(out)
    reg=json.loads((out/'registration.json').read_text())
    if reg['catalog_sha256']!=sha(catalog.__file__) or reg['replay_sha256']!=sha(replay.__file__):
        raise ValueError('registered code changed')
    initial=json.loads((source/'registration.json').read_text())
    cs=reg['candidates']
    if initial['candidates']!=cs[:500]:raise ValueError('initial rules changed')
    if (out/'summary.json').exists():raise ValueError('grid already completed')
    events=list(replay.read_jsonl(source/'normalized.jsonl.gz'));metrics=[];times={}
    for name,depth,cost in SCENARIOS:
        t=time.perf_counter();chosen=cs[500:] if name=='full_depth' else cs
        result=replay.replay_events(events,chosen,catalog.signal,replay.ReplayConfig(
            shares=5,require_constraints=True,depth_fraction=depth,extra_cost_per_filled_share_usd=cost))
        with gzip.open(out/(name+'_attempts.jsonl.gz'),'wt',compresslevel=1) as f:
            if name=='full_depth':
                with gzip.open(source/'attempts.jsonl.gz','rt') as original:
                    for line in original:f.write(line)
            for r in result.pop('attempts'):f.write(json.dumps(r)+'\n')
        summaries=result['summary']
        if name=='full_depth':summaries=json.loads((source/'result.json').read_text())['summary']+summaries
        metrics.extend({'scenario':name,**r} for r in summaries)
        times[name]=time.perf_counter()-t
        (out/(name+'_audit.json')).write_text(json.dumps(result,indent=2)+'\n')
        print(name,'complete',flush=True)
    (out/'summary.json').write_text(json.dumps({'metrics':metrics,'times':times,
        'normalized_sha256':sha(source/'normalized.jsonl.gz'),'registered':720,
        'formal_oos_result':None,'qualified_independent_strategies':0},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    p.add_argument('--source');p.add_argument('--register',action='store_true');a=p.parse_args()
    if a.register:registration(a.out)
    else:run(a.source,a.out)
