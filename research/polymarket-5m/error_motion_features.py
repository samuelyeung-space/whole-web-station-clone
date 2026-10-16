"""Receipt-causal model/Poly error EMA derivatives; no outcome access or fitting.

TWAP variance is an explicitly hypothetical Brownian variance-normalization
ablation, NOT reconstruction of official strike, TWAP samples or fair value.
"""
import argparse,gzip,hashlib,json,math
from collections import Counter
from pathlib import Path
from statistics import NormalDist

N=NormalDist()
SOURCE_FEATURES=('book_5','mid_up','tau_s')

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def variance(tau):
    # Brownian integrated mean over final W=60s; proxy units are seconds.
    return tau-40 if tau>=60 else tau**3/(3*60**2)

class State:
    def __init__(self,h):self.h=h;self.reset()
    def reset(self):self.t=None;self.n=0;self.level=None;self.v=0.;self.a=0.;self.j=0.;self.q=None;self.dq=0.
    def update(self,t,e,q):
        if self.t is None or t-self.t!=1000:self.reset()
        alpha=1-math.exp(-1/self.h)
        if self.level is None:self.level=e;self.q=q
        else:
            level=self.level+alpha*(e-self.level)
            v=self.v+alpha*((level-self.level)-self.v)
            a=self.a+alpha*((v-self.v)-self.a)
            j=self.j+alpha*((a-self.a)-self.j)
            dq=self.dq+alpha*((q-self.q)-self.dq)
            self.level,self.v,self.a,self.j,self.dq=level,v,a,j,dq
        self.q=q;self.t=t;self.n+=1
        if self.n<4*self.h:return {}
        return dict(e=self.level,v=self.v,a=self.a,j=self.j,dq=self.dq)

class Enricher:
    def __init__(self):self.states={};self.last={}
    def process(self,event):
        if event['type']!='decision':return event
        mid=event['market_id'];t=event['recv_ms'];f=event['features'];av=event['feature_available_ms']
        ss=self.states.setdefault(mid,{(n,h):State(h) for n in ('plain','twap_variance') for h in (5,15)})
        good=all(type(f.get(k)) in (float,int) and math.isfinite(f[k]) and
                 type(av.get(k)) in (float,int) and av[k]<=t for k in SOURCE_FEATURES)
        good=good and 0<f['book_5']<1 and 0<f['mid_up']<1 and 0<f['tau_s']<=300
        if not good:
            for st in ss.values():st.reset()
            return event
        q=f['book_5'];p=f['mid_up'];tau=f['tau_s']
        regime=tau>=60
        if mid in self.last and regime!=self.last[mid]:
            # V is C1 but not C2 at60s; do not interpret the join as jerk alpha.
            for (norm,h),st in ss.items():
                if norm=='twap_variance':st.reset()
        self.last[mid]=regime
        values={'plain':math.log(q/(1-q))-math.log(p/(1-p)),
                'twap_variance':(N.inv_cdf(q)-N.inv_cdf(p))*math.sqrt(variance(tau))}
        for (norm,h),st in ss.items():
            extra=st.update(t,values[norm],q)
            for k,v in extra.items():
                name=f'eco_{norm}_{h}_{k}'
                if name in f:raise ValueError('derived feature collision')
                f[name]=v;av[name]=t
        return event

def prepare(inputs,out,source_registry):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    known=json.loads(Path(source_registry).read_text())
    hashes={Path(x['output']).name:x['output_sha256'] for x in known['inputs']}
    code={n:sha(Path(__file__).with_name(n)) for n in ('error_motion_strategy.py','error_motion_features.py')}
    registry={'schema':'eco-development-registration-v1','role':'already_consumed_development',
      'rules':__import__('error_motion_strategy').candidates(),'code_sha256':code,
      'normalization_assumption':'Brownian final60s integrated mean variance, not official TWAP fair value',
      'frozen_scores_unchanged':True,'formal_oos_result':None,'inputs':[]}
    for path in map(Path,inputs):
        digest=sha(path)
        if digest!=hashes.get(path.name):raise ValueError('source input hash mismatch')
        registry['inputs'].append({'input':str(path),'sha256':digest,'output':str(out/path.name)})
    (out/'registration.json').write_text(json.dumps(registry,indent=2)+'\n')
    for row in registry['inputs']:
        counts=Counter();en=Enricher()
        with gzip.open(row['input'],'rt') as src,gzip.open(row['output'],'wt',compresslevel=1) as dst:
            for line in src:
                e=json.loads(line)
                if e['type']=='dataset':
                    if e['split']!='development':raise ValueError('nondevelopment input prohibited')
                    e['dataset_id']+=':ECO-v1';e['eco_code_sha256']=code
                e=en.process(e)
                if e['type']=='decision':
                    counts['decisions']+=1
                    for h in (5,15):
                        if f'eco_plain_{h}_v' in e['features']:counts[f'valid_h{h}']+=1
                dst.write(json.dumps(e,separators=(',',':'),allow_nan=False)+'\n')
        row['output_sha256']=sha(row['output']);row['counts']=dict(counts)
        (out/'registration.json').write_text(json.dumps(registry,indent=2)+'\n')
        print(json.dumps({'completed':Path(row['output']).name,'counts':dict(counts)}),flush=True)
    return registry

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',nargs='+',required=True);p.add_argument('--out',required=True);p.add_argument('--source-registry',required=True);a=p.parse_args();prepare(a.input,a.out,a.source_registry)
