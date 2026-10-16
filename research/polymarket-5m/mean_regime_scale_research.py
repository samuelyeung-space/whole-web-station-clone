"""Fixed total 100 shares: initial50, optional50 after confirmed fill proxy.
Not live execution. 250ms fill feedback is a declared sensitivity assumption.
"""
import sys,json,gzip
from pathlib import Path
import mean_regime_research as mr
import mean_regime_scale_execution as ex
from joint_probability_research import dump,sha

def main():
 path,prices,scorepath,out=sys.argv[1:];out=Path(out);out.mkdir(parents=True,exist_ok=True)
 base=[c for c in mr.candidates() if c['window']==60 and c['edge']==.04 and c['band']==1.]
 cs=[]
 for c in base:
  for mode,wait in [('none',0),('pullback',10000),('pullback',30000),('breakout',10000),('breakout',30000)]:
   cs.append({**c,'id':f'SCALE-{len(cs)+1:03}','max_entries':1 if mode=='none' else 2,'scale_mode':mode,'cooldown_ms':wait,'feedback_ms':250})
 dump(out/'registration.json',{'candidates':cs,'input_sha256':{p:sha(p) for p in [path,prices,scorepath]},'signal_sha256':sha(mr.__file__),'execution_sha256':sha(ex.__file__),'code_sha256':sha(__file__),'maximum_shares_per_market':100,'each_entry_shares':50,'constraints':['prior fill known only after assumed match+250ms','first failed entry blocks all scaling','same side only','base signal must still hold','at least1cent pullback or breakout from first limit','single shared1000 cash ledger per candidate; not across alternatives'],'split':'consumed Oct9 development','formal_oos_result':None})
 scores={}
 for l in gzip.open(scorepath,'rt'):
  r=json.loads(l);scores[(r['market_id'],r['recv_ms'])]=r['scores']
 ev,coverage=mr.enrich(path,prices,scores);dump(out/'coverage.json',coverage);metrics=[]
 for latency in [500,600,1000]:
  r=ex.replay_events(ev,cs,mr.signal,ex.ReplayConfig(latencies_ms=(latency,),shares=50,full_or_zero=True,extra_cost_per_filled_share_usd=.01,require_constraints=True));a=r.pop('attempts')
  with gzip.open(out/f'attempts_{latency}.jsonl.gz','wt') as f:
   for row in a:f.write(json.dumps(row)+'\n')
  dump(out/f'audit_{latency}.json',r);metrics.extend(r['summary']);print(latency,'complete',flush=True)
 dump(out/'metrics.json',metrics)
if __name__=='__main__':main()
