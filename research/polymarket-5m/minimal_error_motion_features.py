"""Reuse frozen ECO State; never fit, interpolate, fill gaps, or read labels."""
import argparse,gzip,json,math
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from error_motion_features import State
from replay_lab import sha
from minimal_error_motion_strategy import candidates

class Enricher:
    def __init__(self):self.states={}
    def process(self,event):
        if event['type']!='decision':return event
        f=event['features'];av=event['feature_available_ms'];t=event['recv_ms'];mid=event['market_id']
        for training in ('fixed','expanded'):
            qkey='mfa_trend_'+training
            good=all(type(f.get(k)) in (int,float) and math.isfinite(f[k]) and
                type(av.get(k)) in (int,float) and av[k]<=t for k in (qkey,'mid_up','tau_s'))
            good=good and 0<f[qkey]<1 and 0<f['mid_up']<1 and 0<f['tau_s']<=300
            for h in (5,15):
                st=self.states.setdefault((mid,training,h),State(h))
                if not good:st.reset();continue
                q=f[qkey];p=f['mid_up'];error=math.log(q/(1-q))-math.log(p/(1-p))
                for k,v in st.update(t,error,q).items():
                    name=f'mem_{training}_{h}_{k}'
                    if name in f:raise ValueError('derived collision')
                    f[name]=v;av[name]=t
        return event

def prepare(cache,out):
    from replay_dataset import verify_cache
    cache=Path(cache).resolve();manifest=verify_cache(cache)
    root=Path(out).resolve();root.mkdir(parents=True,exist_ok=False)
    names=('minimal_error_motion_features.py','minimal_error_motion_strategy.py','error_motion_features.py')
    reg={'schema':'minimal-error-motion-v1','registered_at_utc':datetime.now(timezone.utc).isoformat(),
        'input_tape_sha256':manifest['tape_sha256'],'source_cache_key':manifest['dataset_key'],
        'input_role':'already_consumed_development','rules':candidates(),'new_rules':8,'matched_controls':8,
        'existing_edge_controls':2,'new_independent_mechanisms':0,'formal_oos_result':None,
        'code_sha256':{n:sha(Path(__file__).with_name(n)) for n in names},
        'policy':'frozen MFA trend fixed/expanded scores; unchanged ECO plain logit EMA5/15, warmup4h; resets on gaps/missing; edge8%; no fitting or old protocol modification',
        'model_discontinuity_warning':'tree-score jumps are possible; derivative is not smooth true probability; matched admission controls required',
        'benchmark':{'cash':1000,'shares':100,'latencies_ms':[500,600,1000],'stresses':['base','half_depth','plus_1cent']}}
    dump=lambda n,v:(root/n).write_text(json.dumps(v,indent=2)+'\n')
    dump('registration.json',reg);en=Enricher();counts=Counter();dest=root/'events.jsonl.gz'
    with gzip.open(cache/'events.jsonl.gz','rt') as src,gzip.open(dest,'wt',compresslevel=1) as dst:
        for line in src:
            e=json.loads(line)
            if e['type']=='dataset':
                if e.get('split')!='development':raise ValueError('development only')
                e['dataset_id']+=':MEM-v1'
            e=en.process(e)
            if e['type']=='decision':
                counts['decisions']+=1
                for training in ('fixed','expanded'):
                    for h in (5,15):
                        if f'mem_{training}_{h}_v' in e['features']:counts[f'{training}_h{h}']+=1
            dst.write(json.dumps(e,separators=(',',':'),allow_nan=False)+'\n')
    dump('completion.json',{'status':'feature_build_only','output':str(dest),'sha256':sha(dest),
        'counts':dict(counts),'formal_oos_result':None})
    print(json.dumps(dict(counts)),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cache',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();prepare(a.cache,a.out)
