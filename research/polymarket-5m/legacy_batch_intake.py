"""Resumeable legacy ingestion with explicit UTC coverage denominators; no PnL."""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from legacy_recording_cache import ingest

def day(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).strftime('%Y-%m-%d')

def worker(item,cache):
    p=Path(item['local_path'])
    if p.stat().st_size!=item['size_bytes']:raise ValueError('archive byte count mismatch')
    result=ingest(p,cache)
    return dict(**item,cache_key=result['cache_key'],cache_path=str(Path(cache)/result['cache_key']),
                reused=result['reused'],elapsed_seconds=result['elapsed_seconds'],manifest=result['manifest'])

def coverage(results):
    per_day=defaultdict(lambda:dict(theoretical_5m_slots=288,book_receipt_slots=set(),
        registered_market_ids=set(),book_rows=0,btc_price_rows=0))
    manifests=[r['manifest'] for r in results if r.get('status')=='ingested']
    for m in manifests:
        for market in m['markets']:
            if market['start_sec'] is not None:
                per_day[day(float(market['start_sec'])*1000)]['registered_market_ids'].add(market['slug'])
        for stream in m['streams']:
            if stream['kind'] not in ('token_snapshot','btc_spot_trade'):continue
            for slot,count in stream['receipt_slot_row_counts'].items():
                date=day(float(slot)*1000)
                if stream['kind']=='token_snapshot':
                    per_day[date]['book_rows']+=count
                    per_day[date]['book_receipt_slots'].add(slot)
                else:per_day[date]['btc_price_rows']+=count
    out={}
    for date,d in sorted(per_day.items()):
        out[date]=dict(theoretical_5m_slots=288,
            slots_with_any_book_receipt=len(d['book_receipt_slots']),
            slots_without_book_receipts=288-len(d['book_receipt_slots']),
            registered_market_ids=len(d['registered_market_ids']),
            book_rows=d['book_rows'],btc_price_rows=d['btc_price_rows'],
            complete_healthy_markets=None,signals=None,attempts=None,fills=None,pnl=None)
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True);p.add_argument('--cache',required=True)
    p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=2)
    a=p.parse_args();started=time.perf_counter()
    items=json.loads(Path(a.manifest).read_text())['items'];results=[]
    identities=[x['artifact_id'] for x in items]
    if len(set(identities))!=len(identities):raise ValueError('duplicate artifact identity')
    if not 1<=a.workers<=4:raise ValueError('workers must be 1..4')
    def save():
        ordered=sorted(results,key=lambda r:r['run_id'])
        report=dict(schema='legacy-batch-intake-v1',results=ordered,daily=coverage(ordered),
            wall_seconds=time.perf_counter()-started,input_artifacts=len(items),
            successful_artifacts=sum(r['status']=='ingested' for r in results),
            strict_execution_ready=False,new_strategy_evaluations=0,formal_oos_result=None,pnl=None,
            semantics='Receipt-slot presence is not continuous healthy coverage. Calendar denominator is 288/day. Counts are not trades. Unclocked rows never become decision inputs.')
        target=Path(a.out);temporary=target.with_suffix('.pending');temporary.write_text(json.dumps(report,indent=2)+'\n');temporary.replace(target)
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        jobs={pool.submit(worker,x,a.cache):x for x in items}
        for job in as_completed(jobs):
            item=jobs[job]
            try:result=dict(status='ingested',**job.result())
            except Exception as error:result=dict(**item,status='failed',error=str(error))
            results.append(result);save()
            print(json.dumps(dict(run_id=item['run_id'],status=result['status'],
                rows=result.get('manifest',{}).get('total_rows'),error=result.get('error'))),flush=True)
    print(json.dumps(dict(daily=coverage(results),wall_seconds=time.perf_counter()-started)),flush=True)

if __name__=='__main__':main()
