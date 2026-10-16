import json,gzip,collections,hashlib,datetime
from pathlib import Path
import sys
p=Path(sys.argv[1])
metrics=json.load(open(p/'metrics.json'));reg=json.load(open(p/'registration.json'));cat={c['id']:c for c in reg['candidates']};groups=collections.defaultdict(list)
for r in metrics:groups[r['candidate_id']].append(r)
counts={}
for scenario in sorted({r['scenario'] for r in metrics}):
 rows=[r for r in metrics if r['scenario']==scenario];by=collections.defaultdict(list)
 for r in rows:by[r['candidate_id']].append(r['net_pnl_usd'])
 counts[scenario]={'evaluated_scenarios':len(rows),'positive_scenarios':sum(r['net_pnl_usd'] is not None and r['net_pnl_usd']>0 for r in rows),'all_three_latencies_positive':sum(len(v)==3 and all(x is not None and x>0 for x in v) for v in by.values())}
robust=[k for k,v in groups.items() if len(v)==21 and all(r['net_pnl_usd'] is not None and r['net_pnl_usd']>0 for r in v)]
selected=[]
for mechanism in ['edge','continuation','exhaustion']:
 ids=[k for k in groups if cat[k]['mechanism']==mechanism]
 def score(k):return min(r['net_pnl_usd'] if r['net_pnl_usd'] is not None else -1e99 for r in groups[k] if r['scenario']=='size100_full_plus1c')
 k=max(ids,key=score);selected.append({'candidate':cat[k],'all21positive':k in robust,'metrics':groups[k]})
logs={};checks={};signatures=collections.defaultdict(list)
for scenario in counts:
 rows=[json.loads(l) for l in gzip.open(p/(scenario+'_attempts.jsonl.gz'),'rt')];logs[scenario]=rows
 if 'full' in scenario:assert all(r['filled_shares'] in (0,100) for r in rows)
 assert all(r.get('cash_after_match') is None or r['cash_after_match']>=-1e-7 for r in rows)
 checks[scenario]={'all_attempts':len(rows),'nonnegative_match_cash':True,'full_or_zero_checked':'full' in scenario}
 for sel in selected:
  k=sel['candidate']['id'];daily=collections.defaultdict(float)
  for r in rows:
   if r['candidate_id']==k and r['net_pnl_usd'] is not None:daily[(r['latency_ms'],datetime.datetime.fromtimestamp(r['decision_ms']/1000,datetime.timezone.utc).strftime('%Y-%m-%d'))]+=r['net_pnl_usd']
  sel.setdefault('daily',{})[scenario]={str(k):v for k,v in daily.items()}
base=logs['size100'];by=collections.defaultdict(list)
for r in base:by[r['candidate_id']].append((r['latency_ms'],r['market_id'],r['decision_ms'],r['side'],r['status'],r['filled_shares']))
for k,v in by.items():signatures[hashlib.sha256(json.dumps(v).encode()).hexdigest()].append(k)
# Hold all other rule settings fixed when comparing feature ablations.
paired=[]
for c in cat.values():
 if not c['model'].startswith('joint'):continue
 other=next(x for x in cat.values() if x['model']=='book_'+str(c['horizon']) and x['mechanism']==c['mechanism'] and x['edge']==c['edge'])
 for r in groups[c['id']]:
  if r['scenario']!='size100_full_plus1c':continue
  q=next(x for x in groups[other['id']] if x['scenario']==r['scenario'] and x['latency_ms']==r['latency_ms'])
  paired.append({'joint':c['id'],'book':other['id'],'latency_ms':r['latency_ms'],'joint_minus_book_pnl':r['net_pnl_usd']-q['net_pnl_usd'] if r['net_pnl_usd'] is not None and q['net_pnl_usd'] is not None else None})
summary={'counts':counts,'all21positive_candidates':robust,'selected_by_mechanism':selected,'unique_trade_signatures_at100':len(signatures),'duplicate_groups':[v for v in signatures.values() if len(v)>1],'execution_checks':checks,'joint_vs_book_pairs':paired,'new_unique_rule_attempts':96,'effective_oos_days':0,'qualification':'development_only; two prediction dates; no confirmed viable strategy; selection includes 96 correlated rules plus historical searches','stage1':'96 same rules registered but no trained predictions due sparse continuous60s features; not 1440 valid backtests','stage2':'partial execution rerun superseded by stage3; overlapping cases not counted twice'}
(p/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps({'counts':counts,'all21positive':len(robust),'selected':[(s['candidate'],s['all21positive'],[(r['latency_ms'],r['net_pnl_usd'],r['full_fills'],r['attempts']) for r in s['metrics'] if r['scenario']=='size100_full_plus1c']) for s in selected],'unique_signatures':len(signatures)},ensure_ascii=False,indent=2))
