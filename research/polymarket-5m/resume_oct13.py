import sys,json,gzip,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'code') if (Path(__file__).parent/'code').is_dir() else str(Path(__file__).parent))
import joint_probability_execution as ex
import joint_probability_research as jp
import joint_portfolio_research as port
root=Path(sys.argv[1]); src=root/'bundle-btc/latency/binance_prices.jsonl.gz'; dest=root/'btc_trades_legacy_units.jsonl.gz'
allrows=list(ex.read_jsonl(src));metadata=[r for r in allrows if r.get('kind')=='metadata']
assert len(metadata)==1 and metadata[0]['schema']=='polymarket-binance-price-sidecar-v1'
rows=[r for r in allrows if r.get('kind')!='metadata'];epochs={r['connection_epoch'] for r in rows}
assert metadata[0]['emitted_event_count']==len(rows)
assert epochs=={1},'Mapping must not silently bridge reconnects'
assert rows[0]['kind']=='connection' and rows[-1]['kind']=='disconnect'
assert all(b['recv_ms']>=a['recv_ms'] for a,b in zip(rows,rows[1:]))
assert all(b['sequence']==a['sequence']+1 for a,b in zip(rows,rows[1:]))
trades=[r for r in rows if r['kind']=='trade']
assert len(trades)==228729
with gzip.open(dest,'wt') as f:
 for r in trades:
  assert r['asset']=='btc' and all(math.isfinite(r[k]) for k in ['source_ts_ms','recv_ms','price']) and r['price']>0
  f.write(json.dumps({'receive_ts':r['recv_ms']/1000,'trade_ts':r['source_ts_ms']/1000,'price':r['price']})+'\n')
jp.dump(root/'fixed_top3/price_mapping_audit.json',{'source_sha256':jp.sha(src),'output_sha256':jp.sha(dest),'rows':len(rows),'trade_rows':len(trades),'mapping':'trade only; source_ts_ms/1000 -> trade_ts; recv_ms/1000 -> receive_ts; no BBO substitution or interpolation','connection_epochs':sorted(epochs),'new_formal_oos_result':None,'purpose':'schema-equivalent retrospective input; existing strategy parameters unchanged'})
port.run(str(root/'endpoint.jsonl.gz'),str(dest),str(root/'fixed_top3/scores.jsonl.gz'),str(root/'fixed_top3/portfolio'))
