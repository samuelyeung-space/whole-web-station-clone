"""Receipt-clock closed-bar first-pullback features. No outcome/volume access.
Bar [t-1000,t) becomes available at t; exact-boundary ticks belong to next bar.
Price is carried at most 3s from its receipt; longer gaps reset all state.
"""
import argparse, gzip, json, math, hashlib
from pathlib import Path
from collections import deque,Counter

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    with gzip.open(p,'rt') as f:
        for l in f:yield json.loads(l)

class PullbackState:
    def __init__(self):self.reset()
    def reset(self):
        self.hist=deque(maxlen=121);self.fast=None;self.slow=None;self.anchor=None;self.last_t=None
        self.ordinal=0;self.excursion=False;self.pending=False;self.used=False;self.bar_open={};self.next_anchor_id=0
    def step(self,t,o,p,bar_s):
        if self.last_t is not None and t!=self.last_t+1000:self.reset()
        self.last_t=t
        self.fast=p if self.fast is None else self.fast+(1-math.exp(-1/15))*(p-self.fast)
        self.slow=p if self.slow is None else self.slow+(1-math.exp(-1/60))*(p-self.slow)
        previous=list(self.hist);self.hist.append((t,p,self.slow,o))
        # Closed 5s bar is available only at its right edge and needs all five bars.
        is_close=t%(bar_s*1000)==0
        if len(previous)<120:return None
        returns=[math.log(previous[i][1]/previous[i-1][1]) for i in range(len(previous)-59,len(previous))]+[math.log(p/previous[-1][1])]
        mean=sum(returns)/len(returns);var=sum((x-mean)**2 for x in returns)/len(returns)
        scale=max(p*math.sqrt(var*60),p*1e-8)
        slope=(self.slow-previous[-10][2])/scale
        s=1 if self.fast>self.slow and slope>.5 else -1 if self.fast<self.slow and slope<-.5 else 0
        z=(p-self.fast)/scale
        if self.anchor is not None and (s!=self.anchor[1] or t-self.anchor[0]>300000):
            self.anchor=None;self.ordinal=0;self.excursion=False;self.pending=False
        # Breakout compares CURRENT close only with PRIOR 60 closed observations.
        levels=[r[1] for r in previous[-60:]]
        if self.anchor is None and s and (p>max(levels) if s==1 else p<min(levels)) and s*z>1:
            self.next_anchor_id+=1;self.anchor=(t,s,self.next_anchor_id);self.ordinal=0;self.excursion=True;self.pending=False
        out={'direction':0,'ordinal':0,'anchor_ms':0,'slope':slope,'z':z}
        if self.anchor is None:return out
        a,ds,aid=self.anchor;out['anchor_ms']=a
        if ds*z>1:self.excursion=True
        if self.excursion and abs(z)<=.5 and t>a:
            self.ordinal+=1;self.pending=True;self.excursion=False;self.used=False
        # One confirmation per retest, consumed even if q later rejects it.
        if self.pending and is_close and not self.used:
            open_px=o if bar_s==1 else previous[-4][3] # first observed trade in the first of five closed bars
            if ds*(p-open_px)>0:
                out.update(direction=ds,ordinal=self.ordinal);self.used=True;self.pending=False
        return out

def closed_bars(rows):
    current=None;o=None;c=None;lastsrc=-math.inf;lastrecv=None
    for r in rows:
        t=float(r['receive_ts'])*1000;bucket=math.floor(t/1000)*1000
        if current is not None and bucket<current:raise ValueError('receipt regression')
        if current is not None and bucket!=current:
            # Causal last-observation bars: no trades is not itself a feed outage.
            # Carry only information actually received <=3s ago; no interpolation.
            for left in range(current,bucket,1000):
                if c is not None and left+1000-lastrecv<=3000:
                    yield left+1000,o if left==current and o is not None else c,c
            o=None
        current=bucket
        if r['trade_ts']<lastsrc:continue
        lastsrc=r['trade_ts'];p=float(r['price'])
        if not math.isfinite(p) or p<=0:raise ValueError('bad price')
        if o is None:o=p
        c=p;lastrecv=t
    # A timer can close the tail without waiting for a FUTURE receipt. Values
    # are valid only while the last actual receipt is <=3s old. The caller joins
    # them to recorded decision timestamps; this never creates execution quotes.
    if current is not None and c is not None:
        for left in range(current,current+3000,1000):
            if left+1000-lastrecv<=3000:
                yield left+1000,o if left==current and o is not None else c,c

def features(rows):
    states={b:PullbackState() for b in (1,5)};out={}
    for t,o,p in closed_bars(rows):
        f={}
        for b,st in states.items():
            r=st.step(t,o,p,b)
            if r is not None:f.update({f'mlq{b}_{k}':v for k,v in r.items()})
        out[t]=f
    return out

def prepare(manifests,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    reg={'schema':'MLQ01-first-pullback-development-v1','split':'already_consumed_development','formal_oos_result':None,
      'attribution':'Researcher defined BTC5m transfer hypothesis; no claim to replicate Martin Luk US-equity returns',
      'frozen_q':'existing book_5 score unchanged, absent score remains absent',
      'rules':{'price_asof_max_age_ms':3000,'ema_seconds':[15,60],'warmup_consecutive_seconds':120,'slope_horizon_seconds':10,'scale':'trailing60 closed 1s log return population SD * price * sqrt60; epsilon1e-8*price','slope_min':.5,'breakout_lookback_seconds':60,'excursion_z':1,'retest_abs_z_max':.5,'anchor_expiry_seconds':300,'confirmation_seconds':[1,5],'paired_modes':['first','all'],'edge':.02,'tau_range':[60,240],'exit':'official_settlement_only'},
      'blocked_variants':['structural_stop_and_timeout exits unsupported by existing engine','smooth-only ablation not run','true-volume AVWAP missing','native equity original strategy not run'],
      'code_sha256':{p:sha(p) for p in [__file__,str(Path(__file__).with_name('mlq_first_pullback.py'))]},'inputs':[]}
    (out/'registration.json').write_text(json.dumps(reg,indent=2))
    for m in manifests:
        for spec in json.loads(Path(m).read_text())['inputs']:
            price=spec['prices_path'];tape=spec['output_path'];dest=out/Path(tape).name
            if dest.exists():raise ValueError('refuse overwrite')
            ff=features(read(price));count=Counter()
            with gzip.open(dest,'wt',compresslevel=1) as f:
                for e in read(tape):
                    if e['type']=='dataset':
                        if e['split']!='development':raise ValueError('nondevelopment')
                        e['dataset_id']+=':MLQ01';e['mlq01_feature_provenance']={'code_sha256':reg['code_sha256'],'prices_sha256':sha(price),'input_tape_sha256':sha(tape)}
                    if e['type']=='decision':
                        extra=ff.get(e['recv_ms'],{});e['features'].update(extra);e['feature_available_ms'].update({k:e['recv_ms'] for k in extra})
                        count['decisions']+=1
                        for b in (1,5):
                            if f'mlq{b}_direction' in extra:count[f'valid_state_b{b}']+=1
                            if extra.get(f'mlq{b}_direction',0):
                                count[f'confirmation_b{b}']+=1
                                count[f'ordinal_b{b}_{extra[f"mlq{b}_ordinal"]}']+=1
                                if 'book_5' in e['features']:count[f'confirmation_with_q_b{b}']+=1
                    f.write(json.dumps(e,separators=(',',':'),allow_nan=False)+'\n')
            reg['inputs'].append({'tape':tape,'tape_sha256':sha(tape),'prices':price,'prices_sha256':sha(price),'output':str(dest),'output_sha256':sha(dest),'counts':dict(count)})
            (out/'registration.json').write_text(json.dumps(reg,indent=2));print(str(dest),dict(count),flush=True)
    return reg
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',action='append',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();prepare(a.manifest,a.out)
