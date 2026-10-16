"""Consumed Oct9 development only: trailing price means, slope and Poly regimes.
Binance receipt-clock prices; a trailing observed-price mean is NOT official settlement TWAP.
"""
import json,gzip,sys,math,hashlib
from pathlib import Path
from collections import deque,defaultdict
import numpy as np
import joint_probability_execution as ex
from joint_probability_research import dump,sha

def candidates():
 cs=[]
 for mechanism in ['trend_pullback','flat_overshoot','btc_jump_poly_lag']:
  for w in [15,60,120]:
   for slope in [False,True]:
    for smooth in [False,True]:
     for band in [.5,1.]:
      for edge in [.04,.08]:
       cs.append({'id':f'MAR-{len(cs)+1:03}','mechanism':mechanism,'window':w,'reference_slope_gate':slope,'smooth_only':smooth,'band':band,'edge':edge,'required_features':['book_5','ask_up','ask_down','fee',f'z{w}',f'slope{w}',f'refslope{w}','btc_r5','poly_eff','poly_move','imbalance']})
 return cs

def signal(c,f):
 p=f['book_5'];u=p-f['ask_up']-f['fee']*f['ask_up']*(1-f['ask_up']);d=1-p-f['ask_down']-f['fee']*f['ask_down']*(1-f['ask_down']);s=1 if u>=d else -1
 if max(u,d)<c['edge'] or not 30<=f['tau_s']<=240:return
 if c['smooth_only'] and not(f['poly_eff']>=.7 and s*f['poly_move']>0):return
 w=c['window'];z=f[f'z{w}'];sl=f[f'slope{w}'];rs=f[f'refslope{w}']
 if c['mechanism']=='trend_pullback':ok=s*sl>.5 and abs(z)<=c['band'] and s*f['btc_r5']>0
 elif c['mechanism']=='flat_overshoot':ok=abs(sl)<=.5 and s*z < -c['band'] and s*f['imbalance']>0
 else:ok=s*f['btc_r5']>=1 and s*z>=c['band'] and s*f['poly_move']<.02
 if c['reference_slope_gate']:
  ok=ok and (abs(rs)<=.5 if c['mechanism']=='flat_overshoot' else s*rs>0)
 return ('Up' if s>0 else 'Down') if ok else None

def build_price_grid(path,times):
 raw=[json.loads(l) for l in gzip.open(path,'rt')];raw.sort(key=lambda r:r['receive_ts']);k=0;last=None;values=[];lastsrc=-math.inf
 for t in times:
  while k<len(raw) and raw[k]['receive_ts']*1000<=t:
   r=raw[k];k+=1
   if r['trade_ts']>=lastsrc:last=r;lastsrc=r['trade_ts']
  values.append(last['price'] if last and t-last['receive_ts']*1000<=3000 else np.nan)
 return np.array(values)

def enrich(path,prices,scores):
 ev=list(ex.read_jsonl(path));decs=[e for e in ev if e['type']=='decision'];times=np.arange(int(min(e['recv_ms'] for e in decs)),int(max(e['recv_ms'] for e in decs))+1,1000);px=build_price_grid(prices,times);feat={};coverage=defaultdict(int)
 for i,t in enumerate(times):
  if i<130 or not np.isfinite(px[i]) or not np.isfinite(px[i-5]):continue
  f={'btc_r5':10000*(px[i]/px[i-5]-1)}
  for w in [15,60,120]:
   a=px[i-w+1:i+1];b=px[i-w-9:i-9]
   if np.isfinite(a).mean()<.9 or np.isfinite(b).mean()<.9:continue
   ma=float(np.nanmean(a));prev=float(np.nanmean(b));sd=max(float(np.nanstd(a)),ma*.0001)
   # slope: change in trailing mean over TEN seconds, in bps.
   f[f'z{w}']=(px[i]-ma)/sd;f[f'slope{w}']=10000*(ma/prev-1)
   # Additional slope of a 60s observed-price average; not settlement TWAP.
   a60=px[i-59:i+1];b60=px[i-69:i-9]
   if np.isfinite(a60).mean()>=.9 and np.isfinite(b60).mean()>=.9:f[f'refslope{w}']=10000*(float(np.nanmean(a60))/float(np.nanmean(b60))-1)
  feat[t]=f
 history=defaultdict(deque)
 for e in ev:
  if e['type']!='decision':continue
  t=e['recv_ms'];k=e['market_id'];f=e['features'];mid=f.get('mid_up');h=history[k]
  if isinstance(mid,(float,int)) and e['feature_available_ms'].get('mid_up',t+1)<=t:
   h.append((t,mid))
  while h and h[0][0]<t-15000:h.popleft()
  sf=scores.get((k,t));extra=dict(feat.get(t,{}))
  if sf:extra.update(sf)
  if len(h)>=12 and h[-1][0]==t and h[-1][0]-h[0][0]>=12000:
   tv=sum(abs(h[i][1]-h[i-1][1]) for i in range(1,len(h)));move=h[-1][1]-h[0][1];extra.update(poly_eff=abs(move)/tv if tv>0 else 0,poly_move=move)
  if isinstance(f.get('imbalance_up'),(int,float)):extra['imbalance']=f['imbalance_up']
  f.update(extra);e['feature_available_ms'].update({n:t for n in extra})
  for w in [15,60,120]:
   if f'z{w}' in extra and f'refslope{w}' in extra and sf and 'poly_eff' in extra:coverage[w]+=1
 return ev,dict(coverage)

def main():
 path,prices,scorepath,out=sys.argv[1:];out=Path(out);out.mkdir(parents=True,exist_ok=True);cs=candidates()
 dump(out/'registration.json',{'candidates':cs,'input_sha256':{p:sha(p) for p in [path,prices,scorepath]},'code_sha256':sha(__file__),'split':'Oct9 already consumed development','model':'prior joint experiment book_5 model frozen before this condition sweep; training labels earlier than Oct9 UTC','price_grid':'1sec local receive asof; maxage3sec; >=90% valid observations; 10sec mean slope; minimum dispersion1bp','reference_slope':'observed BTC 60sec time-grid mean; NOT official Chainlink settlement TWAP','selection_bias':'book_5 selected after previous development results; all candidates count toward cumulative search','blocked':['official TWAP slope comparison absent official TWAP samples','aggressor-side volume absent','maker queue fills absent','scale-in stateful execution not included'],'formal_oos_result':None})
 scores={}
 for l in gzip.open(scorepath,'rt'):
  r=json.loads(l);scores[(r['market_id'],r['recv_ms'])]=r['scores']
 ev,coverage=enrich(path,prices,scores);dump(out/'coverage.json',coverage);metrics=[]
 for name,depth,cost,full in [('partial100',1,0,False),('halfdepth100',.5,0,False),('full100',1,0,True),('full100_plus1c',1,.01,True)]:
  result=ex.replay_events(ev,cs,signal,ex.ReplayConfig(shares=100,depth_fraction=depth,extra_cost_per_filled_share_usd=cost,full_or_zero=full,require_constraints=True));attempts=result.pop('attempts')
  with gzip.open(out/(name+'_attempts.jsonl.gz'),'wt') as f:
   for a in attempts:f.write(json.dumps(a)+'\n')
  dump(out/(name+'_audit.json'),result)
  metrics.extend({'scenario':name,**r} for r in result['summary']);print(name,'complete',flush=True)
 dump(out/'metrics.json',metrics);dump(out/'completion.json',{'registered_candidates':len(cs),'scenario_rows':len(metrics),'valid_evaluated_rows':sum(r['attempts']>0 for r in metrics),'confirmed_strategies':0,'formal_oos_result':None})
if __name__=='__main__':main()
