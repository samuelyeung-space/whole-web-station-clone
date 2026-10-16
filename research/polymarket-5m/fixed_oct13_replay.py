"""Retrospective fixed-rule replay; never a new formal OOS qualification."""
import sys, glob, json, gzip, subprocess
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).parent/'code') if (Path(__file__).parent/'code').is_dir() else str(Path(__file__).parent))
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
import joint_probability_research as jp
import joint_probability_execution as ex
import joint_portfolio_research as port
import mine500_endpoint as ep
import mine500_deep_batch as batch

root=Path(sys.argv[1]); out=root/'fixed_top3'; out.mkdir(exist_ok=False)
source=root/'bundle-btc/strict/source_events.jsonl.gz'
normalized=root/'constraints/normalized_with_constraints.jsonl.gz'
endpoint=root/'endpoint.jsonl.gz'; audit={}
batch.write_gzip(endpoint, ep.augment(ex.read_jsonl(normalized),ex.read_jsonl(source),audit))
jp.dump(out/'endpoint_audit.json',audit)
print('endpoint complete',flush=True)
trainpaths=sorted(glob.glob(sys.argv[2]))
assert len(trainpaths)==6
rows,labels,markets=jp.features(trainpaths)
cutoff=20735*86400000
train=[r for r in rows if r['day']<20735 and r['market'] in labels and labels[r['market']][1]<cutoff and markets[r['market']]['end_ms']<cutoff]
test,_,_=jp.features([str(endpoint)])
ids={r['market'] for r in train}; counts=Counter(r['market'] for r in train)
assert not ids.intersection(r['market'] for r in test)
x=np.array([r['vectors']['book_5'] for r in train]);y=np.array([labels[r['market']][0]-r['p'] for r in train])
fingerprint=ex.fingerprint({'x':x.tolist(),'y':y.tolist(),'markets':sorted(ids)})
expected='d79a7cba1d60160745ab2cc051369bb56229c27187fb1dc1de6685d44be928f3'
assert fingerprint==expected,(fingerprint,len(train),len(ids))
w=np.array([1/counts[r['market']] for r in train]);w=w/w.mean()
model=HistGradientBoostingRegressor(max_iter=80,max_leaf_nodes=7,l2_regularization=10,learning_rate=.05,min_samples_leaf=100,random_state=17,early_stopping=False)
with threadpool_limits(limits=1):
 model.fit(x,y,sample_weight=w)
 pred=model.predict(np.array([r['vectors']['book_5'] for r in test])) if test else []
scorepath=out/'scores.jsonl.gz'
with gzip.open(scorepath,'wt') as f:
 for r,prediction in zip(test,pred):
  v={k:v for k,v in r.items() if k not in ['vectors','market','t','day','p','tau']}
  v['book_5']=float(np.clip(r['p']+prediction,.001,.999))
  f.write(json.dumps({'market_id':r['market'],'recv_ms':r['t'],'scores':v})+'\n')
jp.dump(out/'fixed_model_audit.json',{'training_cutoff_ms':cutoff,'training_rows':len(train),'training_markets':len(ids),'training_hash':fingerprint,'expected_hash':expected,'prediction_rows':len(test),'prediction_markets':len({r['market'] for r in test}),'formal_oos_result':None,'classification':'retrospective validation; rules selected 2026-10-16; no refit on Oct13','input_hashes':{p:jp.sha(p) for p in trainpaths+[str(endpoint)]}})
print('fixed model hash verified',flush=True)
subprocess.run([sys.executable,str(Path(__file__).parent/'resume_oct13.py'),str(root)],check=True)
subprocess.run([sys.executable,str(Path(__file__).parent/'fixed_oct13_components.py'),str(root)],check=True)
