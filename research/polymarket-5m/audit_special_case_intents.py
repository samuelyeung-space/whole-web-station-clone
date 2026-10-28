"""Consumed-data minute-path audit; intents only, no trading or PnL."""
import argparse,gzip,json,hashlib,math
from collections import Counter
from pathlib import Path
from special_case_trend import MultiTimeframe,SpecialCase

class Minutes:
 def __init__(self):
  self.buckets={};self.connected=False;self.epoch=None;self.last=None;self.clock=None
 def ingest(self,r):
  t=r['recv_ms'];kind=r['kind'];epoch=r.get('connection_epoch')
  if self.clock is not None and t<self.clock:raise ValueError('receipt regression')
  self.clock=t;b=int(t//60000)*60000
  x=self.buckets.setdefault(b,dict(start_ms=b,end_ms=b+60000,prices=[],bad=[],prior=self.last,
                                 connected_at_first_event=self.connected))
  if epoch!=self.epoch:
   x['bad'].append('epoch_change');self.epoch=epoch;self.last=None
  if kind=='spot_connection':
   self.connected=True;x['bad'].append('connection');return
  if kind in ('spot_disconnect','spot_error'):
   self.connected=False;x['bad'].append('disconnect');return
  if kind!='spot_trade':return
  price=r.get('price')
  if not self.connected or type(price) not in (int,float) or not math.isfinite(price) or price<=0:
   x['bad'].append('invalid_trade');return
  if self.last is not None and t-self.last>5000:x['bad'].append('trade_gap')
  x['prices'].append((t,price));self.last=t
 def close(self,start):
  x=self.buckets.get(start);end=start+60000
  out=dict(start_ms=start,end_ms=end,available_ms=end,complete=False)
  if not x or not x['prices']:return dict(out,reasons=['missing_trades'])
  rows=x['prices'];reasons=list(set(x['bad']))
  if not x['connected_at_first_event']:reasons.append('not_connected_at_start')
  if x['prior'] is None or not 0<=start-x['prior']<=1000:reasons.append('missing_fresh_preopen_trade')
  if rows[0][0]-start>1000:reasons.append('late_first_trade')
  if end-rows[-1][0]>1000:reasons.append('stale_close')
  prices=[p for _,p in rows]
  return dict(out,open=prices[0],high=max(prices),low=min(prices),close=prices[-1],
              first_recv_ms=rows[0][0],last_recv_ms=rows[-1][0],trade_count=len(rows),
              complete=not reasons,reasons=reasons)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--protocol',required=True);p.add_argument('--output',required=True);a=p.parse_args();proto=json.load(open(a.protocol))
 for v in proto['inputs'].values():
  if sha(v['path'])!=v['sha256']:raise ValueError('input hash mismatch')
 for n,h in proto['code_sha256'].items():
  if sha(Path(__file__).with_name(n))!=h:raise ValueError('code hash mismatch')
 markets={}
 for l in open(proto['inputs']['metadata']['path']):
  r=json.loads(l)
  if r['type']=='market':markets.setdefault(r['market_id'],r)
 targets={}
 for m in markets.values():
  for minute in range(5):targets.setdefault(m['start_ms']+minute*60000,[]).append((m,minute))
 try:
  import orjson
  loads=orjson.loads
 except ImportError:loads=json.loads
 def source():
  with gzip.open(proto['inputs']['source']['path'],'rb') as f:
   for l in f:
    r=loads(l)
    if r.get('stream')=='spot':yield r
 ctx=MultiTimeframe();minutes=Minutes();it=iter(source());r=next(it,None);models={};audit={};intents=[];counts=Counter()
 for now,items in sorted(targets.items()):
  while r is not None and r['recv_ms']<=now:
   ctx.ingest(r);minutes.ingest(r);counts[r['kind']]+=1;r=next(it,None)
  snap=ctx.snapshot(now);epochs=tuple(snap[str(k)]['ctx_epoch'] for k in (300000,900000))
  for m,i in items:
   mid=m['market_id']
   if i==0:
    model=SpecialCase(now,snap,snap['300000']['ctx_price'])
    if m['recv_ms']>now:model.reason='metadata_unknown_at_open'
    models[mid]=model;audit[mid]=dict(market_id=mid,opening_ms=now,opening_context=snap,bars=[],intents=[])
   else:
    bar=minutes.close(now-60000);model=models[mid]
    out=model.closed_minute(bar,now,epochs);audit[mid]['bars'].append(bar)
    for event in out:
     event=dict(event,market_id=mid,parent_direction=model.direction)
     audit[mid]['intents'].append(event);intents.append(event)
 for mid,model in models.items():
  audit[mid].update(reason=model.reason,entry_checked=model.checked,entry_intent=model.entered,
                    fourth_minute_checked=model.exit_checked)
 result=dict(schema='special-case-intents-audit-v1',protocol_sha256=sha(a.protocol),
    evidence_class='consumed_one_day_4.502669h_development',market_count=len(audit),
    entry_intents=sum(e['kind']=='ENTRY_INTENT' for e in intents),
    exit_or_hedge_intents=sum(e['kind']=='EXIT_OR_HEDGE_INTENT' for e in intents),
    reason_counts=dict(Counter(r['reason'] or ('entry_intent' if r['entry_intent'] else 'valid_no_entry') for r in audit.values())),
    source_counts=dict(counts),markets=list(audit.values()),intents=intents,
    financial_replays=0,net_pnl_usd=None,full_week_pnl=None,formal_oos_result=None,
    notes=['received trade minute OHLC; not exchange/chart or settlement labels',
           'no threshold retuning; missing/gapped bars fail closed',
           'all56 markets retained; sell-held and hedge counterfactual executions pending'])
 Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k not in ('markets','intents','notes')}))
if __name__=='__main__':main()
