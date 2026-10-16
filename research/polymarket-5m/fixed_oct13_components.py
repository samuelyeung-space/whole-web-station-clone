import sys,json,gzip
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'code') if (Path(__file__).parent/'code').is_dir() else str(Path(__file__).parent))
import joint_probability_execution as ex
import joint_probability_research as jp
import joint_portfolio_research as port
import mean_regime_research as ma
root=Path(sys.argv[1]); out=root/'fixed_top3/components';out.mkdir(exist_ok=False)
scores={(r['market_id'],r['recv_ms']):r['scores'] for r in ex.read_jsonl(root/'fixed_top3/scores.jsonl.gz')}
events,coverage=ma.enrich(str(root/'endpoint.jsonl.gz'),str(root/'btc_trades_legacy_units.jsonl.gz'),scores)
metrics=[]
for name,qty,depth,full,cost in [('partial5',5,1,False,0),('partial20',20,1,False,0),('partial100_plus1c',100,1,False,.01),('full100_plus1c',100,1,True,.01),('halfdepth100_plus1c',100,.5,False,.01)]:
 for c,fn in port.selected_rules():
  result=ex.replay_events(events,[c],fn,ex.ReplayConfig(shares=qty,initial_cash_usd=1000,depth_fraction=depth,full_or_zero=full,extra_cost_per_filled_share_usd=cost,require_constraints=True))
  stem=name+'_'+c['id']
  with gzip.open(out/(stem+'_attempts.jsonl.gz'),'wt') as f:
   for a in result.pop('attempts'):f.write(json.dumps(a)+'\n')
  jp.dump(out/(stem+'_audit.json'),result)
  metrics.extend({'scenario':name,**r} for r in result['summary'])
 print(name,'complete',flush=True)
jp.dump(out/'metrics.json',metrics)
jp.dump(out/'coverage.json',coverage)
