"""Feature-readiness audit on original received trades, not a financial replay."""
import argparse,gzip,json,hashlib
from pathlib import Path
from collections import Counter
from special_case_trend import MultiTimeframe

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--protocol',required=True);p.add_argument('--output',required=True);a=p.parse_args();proto=json.load(open(a.protocol))
 for f in proto['inputs'].values():
  if sha(f['path'])!=f['sha256']:raise ValueError('input hash mismatch')
 for n,v in proto['code_sha256'].items():
  if sha(Path(__file__).with_name(n))!=v:raise ValueError('code hash mismatch')
 markets={}
 for l in open(proto['inputs']['metadata']['path']):
  r=json.loads(l)
  if r['type']=='market':markets.setdefault(r['market_id'],r)
 ctx=MultiTimeframe();counts=Counter();rows=[]
 def source():
  with gzip.open(proto['inputs']['source']['path'],'rt') as f:
   for l in f:
    r=json.loads(l)
    if r.get('stream')=='spot':yield r
 it=iter(source());r=next(it,None)
 for m in sorted(markets.values(),key=lambda x:x['start_ms']):
  now=m['start_ms']
  while r is not None and r['recv_ms']<=now:
   ctx.ingest(r);counts[r['kind']]+=1;r=next(it,None)
  snap=ctx.snapshot(now);five=snap['300000'];fifteen=snap['900000']
  known=m['recv_ms']<=now
  ready=known and all(snap[k]['ctx_epoch'] is not None for k in ('300000','900000'))
  rows.append(dict(market_id=m['market_id'],opening_ms=now,metadata_known_at_open=known,context=snap,both_ready=ready,
    directions_agree=ready and five['ctx_st_direction']==fifteen['ctx_st_direction']))
  counts['both_ready' if ready else 'opening_unavailable']+=1
 out=dict(classification='consumed_development_feature_audit_not_financial',protocol_sha256=sha(a.protocol),markets=len(rows),counts=dict(counts),openings=rows,context_audit=ctx.audit(),financial_replays=0,net_pnl_usd=None,full_week_pnl=None,formal_oos_result=None,notes=['original receipt-time spot trades; never1s-projection OHLC','ATR10/Supertrend3 andfirstpartialbucketdiscarded; no threshold tuning','snapshot requires SMA slope11 closed bars in shared existing implementation','third/fourth-minute intents unit-tested but not replayed here','exit bids/complement ask capacity and cash/inventory execution still pending'])
 Path(a.output).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(dict(markets=len(rows),counts=dict(counts),net_pnl_usd=None)))
if __name__=='__main__':main()
