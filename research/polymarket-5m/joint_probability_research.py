"""Development-only causal joint probability / book / derivative ablation.
No network, orders or production configuration. Frozen grids before results.
"""
import json,gzip,glob,hashlib,heapq,math,sys
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
import joint_probability_execution as ex

VERSION='joint-probability-development-v3-endpoint'
def dump(p,x): Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def events(paths):
 streams=[(e for e in ex.read_jsonl(p) if e['type']!='dataset') for p in paths]
 yield from heapq.merge(*streams,key=lambda e:e['recv_ms'])
def finite(x):return isinstance(x,(float,int)) and math.isfinite(x)
def features(paths):
 history={};books={};rows=[];labels={};markets={}
 for e in events(paths):
  k=e.get('market_id');t=e['recv_ms'];typ=e['type']
  if typ=='market':markets[k]=e
  elif typ=='outcome':labels[k]=(int(e['winner']=='Up'),max(t,e['resolved_ms']))
  elif typ=='book':books[k]=e
  elif typ=='decision':
   f=e['features'];av=e['feature_available_ms'];need=['mid_up','tau_s','ep_r_5s','ep_r_15s','ep_r_60s','ep_r_30s','imbalance_up']
   if any(not finite(f.get(n)) or not finite(av.get(n)) or av[n]>t for n in need):continue
   b=books.get(k,{}); asks=b.get('up_asks',[]);bids=b.get('up_bids',[]);down=b.get('down_asks',[])
   if not asks or not bids or not down or not b.get('healthy') or not finite(b.get('fee_rate')):continue
   if any(t-b.get(n,-1e20)>250 or b.get(n,t+1)>t for n in ['up_recv_ms','down_recv_ms']):continue
   p=max(.001,min(.999,f['mid_up']));z=math.log(p/(1-p));tau=f['tau_s'];spread=asks[0][0]-bids[0][0]
   qa=sum(v for _,v in asks[:3]);qb=sum(v for _,v in bids[:3]);imb=(qb-qa)/(qa+qb)
   micro=(asks[0][0]*bids[0][1]+bids[0][0]*asks[0][1])/(bids[0][1]+asks[0][1])
   row={'t':t,'market':k,'day':int(t//86400000),'p':p,'tau':tau,'ask_up':asks[0][0],'ask_down':down[0][0],'fee':b['fee_rate'],'r5':f['ep_r_5s'],'vectors':{}}
   for horizon in [5,15]:
    key=(k,horizon);old=history.get(key);dt=(t-old[0])/1000 if old else 0
    if not old or dt<=0 or dt>2: history[key]=(t,z,0.,0.,0.,imb);continue
    a=1-math.exp(-dt/horizon);smooth=old[1]+a*(z-old[1]);v=old[2]+a*((smooth-old[1])/dt-old[2]);acc=old[3]+a*((v-old[2])/dt-old[3]);jerk=old[4]+a*((acc-old[3])/dt-old[4]);di=(imb-old[5])/dt
    history[key]=(t,smooth,v,acc,jerk,imb)
    base=[p,z,tau/300,(p-.5)/math.sqrt(max(tau,1)),z*math.log1p(tau),spread]
    trend=base+[f[n] for n in ['ep_r_5s','ep_r_15s','ep_r_60s','ep_r_30s']]+[f['ep_r_5s']-f['ep_r_15s']/3,f['ep_r_15s']-f['ep_r_60s']/4]
    book=trend+[imb,f['imbalance_up'],micro-p,math.log1p(qa+qb),di]
    deriv=book+[smooth-z,v,acc,jerk,v*row['r5'],acc*imb]
    for name,x in zip(['time','trend','book','joint'],[base,trend,book,deriv]):row['vectors'][f'{name}_{horizon}']=x
    row[f'v{horizon}']=v;row[f'a{horizon}']=acc;row[f'j{horizon}']=jerk
   if len(row['vectors'])==8: rows.append(row)
 return rows,labels,markets

def catalog():
 out=[]
 for h in [5,15]:
  for model in ['time','trend','book','joint']:
   for mechanism in ['edge','continuation','exhaustion']:
    for edge in [.02,.04,.06,.08]:
     key=f'{model}_{h}';out.append({'id':f'JPR-{len(out)+1:03}','model':key,'mechanism':mechanism,'horizon':h,'edge':edge,'required_features':[key,'ask_up','ask_down','fee','r5',f'v{h}',f'a{h}']})
 return out

def signal(c,f):
 p=f[c['model']];up=p-f['ask_up']-f['fee']*f['ask_up']*(1-f['ask_up']);dn=1-p-f['ask_down']-f['fee']*f['ask_down']*(1-f['ask_down']);s=1 if up>=dn else -1
 if max(up,dn)<c['edge'] or not 15<=f['tau_s']<=240:return None
 v=f['v'+str(c['horizon'])];a=f['a'+str(c['horizon'])]
 if c['mechanism']=='continuation' and not(s*f['r5']>0 and s*v>0 and s*a>0):return None
 if c['mechanism']=='exhaustion' and not(s*v<0 and s*a>0):return None
 return 'Up' if s==1 else 'Down'

def main():
 paths=sorted(glob.glob(sys.argv[1]));out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True);cs=catalog()
 dump(out/'registration.json',{'version':VERSION,'candidates':cs,'input_sha256':{p:sha(p) for p in paths},'code_sha256':sha(__file__),'execution_sha256':sha(ex.__file__),'split':'consumed development; chronological prediction is NOT untouched OOS','training':'earlier UTC dates only; official outcome locally known before cutoff; market-balanced weights','model':{'max_iter':80,'max_leaf_nodes':7,'l2_regularization':10,'learning_rate':.05,'min_samples_leaf':100,'random_state':17},'limitations':['No true aggressor side, TWAP sample path or opening benchmark: empirical time calibration only','Snapshot depth changes do not identify cancellation/trade/addition','Causal EMA derivatives, not continuous executable quotes','Independent candidate cash ledgers; cannot sum strategies','Outcome receipt is capital-release proxy; not observed redemption'],'formal_oos_result':None})
 rows,labels,markets=features(paths);scores={};audit=[]
 for day in sorted({r['day'] for r in rows}):
  train=[r for r in rows if r['day']<day and r['market'] in labels and labels[r['market']][1]<day*86400000 and markets[r['market']]['end_ms']<day*86400000];test=[r for r in rows if r['day']==day]
  ids={r['market'] for r in train};counts=defaultdict(int)
  for r in train:counts[r['market']]+=1
  if len(ids)<40: audit.append({'day':day,'train_markets':len(ids),'status':'warmup_no_fit'});continue
  assert not ids.intersection(r['market'] for r in test)
  y=np.array([labels[r['market']][0]-r['p'] for r in train]);w=np.array([1/counts[r['market']] for r in train]);w=w/w.mean()
  for key in test[0]['vectors']:
   x=np.array([r['vectors'][key] for r in train]);xt=np.array([r['vectors'][key] for r in test]);model=HistGradientBoostingRegressor(max_iter=80,max_leaf_nodes=7,l2_regularization=10,learning_rate=.05,min_samples_leaf=100,random_state=17,early_stopping=False)
   with threadpool_limits(limits=1):model.fit(x,y,sample_weight=w);pred=model.predict(xt)
   audit.append({'day':day,'model':key,'train_rows':len(train),'train_markets':len(ids),'test_rows':len(test),'test_markets':len({r['market'] for r in test}),'training_hash':ex.fingerprint({'x':x.tolist(),'y':y.tolist(),'markets':sorted(ids)}),'latest_label_ms':max(labels[k][1] for k in ids)})
   for r,prediction in zip(test,pred):scores.setdefault((r['market'],r['t']),{k:v for k,v in r.items() if k not in ['vectors','market','t','day','p','tau']})[key]=float(np.clip(r['p']+prediction,.001,.999))
 dump(out/'model_audit.json',audit)
 with gzip.open(out/'scores.jsonl.gz','wt') as f:
  for (k,t),v in scores.items():f.write(json.dumps({'market_id':k,'recv_ms':t,'scores':v})+'\n')
 headers=[next(ex.read_jsonl(p)) for p in paths];intervals=sorted([[h.get('coverage_start_ms',h['recv_ms']),h['coverage_end_ms']] for h in headers]);merged=[]
 for a,b in intervals:
  if merged and a<=merged[-1][1]:merged[-1][1]=max(b,merged[-1][1])
  else:merged.append([a,b])
 header={**headers[0],'recv_ms':min(h['recv_ms'] for h in headers),'dataset_id':VERSION,'observed_intervals_ms':merged,'coverage_end_ms':max(b for a,b in merged),'split':'development'}
 def replay_stream():
  yield header
  for e in events(paths):
   if e['type']=='decision':
    s=scores.get((e['market_id'],e['recv_ms']))
    if s is None:continue
    e['features'].update(s);e['feature_available_ms'].update({k:e['recv_ms'] for k in s})
   yield e
 metrics=[]
 for name,qty,depth,cost,full in [('size5',5,1,0,False),('size20',20,1,0,False),('size100',100,1,0,False),('size100_halfdepth',100,.5,0,False),('size100_plus1c',100,1,.01,False),('size100_full',100,1,0,True),('size100_full_plus1c',100,1,.01,True)]:
  result=ex.replay_events(replay_stream(),cs,signal,ex.ReplayConfig(full_or_zero=full,shares=qty,depth_fraction=depth,extra_cost_per_filled_share_usd=cost,require_constraints=True))
  with gzip.open(out/(name+'_attempts.jsonl.gz'),'wt') as f:
   for a in result.pop('attempts'):f.write(json.dumps(a)+'\n')
  dump(out/(name+'_audit.json'),result)
  for r in result['summary']:metrics.append({'scenario':name,**r})
  print(name,'complete',flush=True)
 dump(out/'metrics.json',metrics);dump(out/'completion.json',{'candidate_count':len(cs),'scenario_count':len(metrics),'feature_rows':len(rows),'prediction_rows':len(scores),'input_markets':len(markets),'official_labels':len(labels),'qualified_strategies':0,'formal_oos_result':None})
if __name__=='__main__':main()
