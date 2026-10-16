"""Engineering parity on an already-consumed tape. Never strategy/PnL evidence."""
import argparse
import bisect
from collections import defaultdict
import gzip
import json
from pathlib import Path
import time
from legacy_receipt_timeline import build, Timeline

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-cache',required=True)
    p.add_argument('--output-root',required=True)
    p.add_argument('--report',required=True)
    a=p.parse_args()
    built=build(a.input_cache,a.output_root)
    t=Timeline(built['cache_path'])
    raw=defaultdict(list)
    manifest=json.loads((Path(a.input_cache)/'manifest.json').read_text())
    for s in manifest['streams']:
        if s['kind']!='token_snapshot':continue
        with gzip.open(Path(a.input_cache)/s['cache_file'],'rt') as f:
            for line in f:
                e=json.loads(line);raw[str(e['raw']['asset_id'])].append(e)
    clocks={}
    for asset,rows in raw.items():
        rows.sort(key=lambda e:e['recv_ms'])
        clocks[asset]=[e['recv_ms'] for e in rows]
    requests=[(asset,e['recv_ms']+0.125) for asset,rows in sorted(raw.items()) for e in rows]
    # Fixed stride; no outcomes, prices or strategy performance used in selection.
    requests=requests[::max(1,len(requests)//1000)][:1000]
    started=time.perf_counter();responses=[]
    for asset,at in requests:responses.append(t.asof('token_snapshot',asset,at,1000))
    query_seconds=time.perf_counter()-started
    for (asset,at),result in zip(requests,responses):
        k=bisect.bisect_left(clocks[asset],at)-1
        latest=clocks[asset][k]
        lo=bisect.bisect_left(clocks[asset],latest)
        payloads={json.dumps(e['raw'],sort_keys=True) for e in raw[asset][lo:k+1]}
        expected='available' if len(payloads)==1 else 'ambiguous_equal_receipt'
        assert result['status']==expected
        if expected=='available':assert result['event']['raw']==raw[asset][k]['raw']
    started=time.perf_counter();counts=defaultdict(int);prior=-float('inf');batches=0
    for at,events in t.batches(t.manifest['recv_min_ms'],t.manifest['recv_max_ms']+1,['token_snapshot','btc_spot_trade']):
        assert at>prior;prior=at;batches+=1
        for e in events:counts[e['kind']]+=1
    scan_seconds=time.perf_counter()-started
    result=dict(schema='legacy-timeline-engineering-benchmark-v1',timeline_manifest=t.manifest,
        cache_reused=built['reused'],hash_verified_cache_open_seconds=built['elapsed_seconds'],
        exact_asof_parity_queries=len(requests),asof_query_seconds=query_seconds,
        receipt_batches=batches,scan_rows=dict(counts),scan_seconds=scan_seconds,
        strategy_backtests=0,pnl=None,formal_oos_result=None,qualified_mechanisms=0)
    Path(a.report).write_text(json.dumps(result,indent=2)+'\n')
    t.close();print(json.dumps({k:v for k,v in result.items() if k!='timeline_manifest'}))

if __name__=='__main__':main()
