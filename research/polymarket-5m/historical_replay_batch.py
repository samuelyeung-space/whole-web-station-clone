"""Resumable offline batch import and one joint chronological development replay.

Keeps every failed input in the batch manifest. Does not sum independently
replayed fragments, fetch data, change strategy rules, or fill missing dates.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

import replay_dataset
import replay_lab
import replay_raw


def dump(path, value):
    path=Path(path);temp=path.with_suffix(path.suffix+'.pending')
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    temp.replace(path)


def inspect_boundary(path, cutoff_ms):
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt') as f:header=json.loads(next(f))
    if header.get('type')!='dataset' or header.get('split')!='development':
        raise ValueError('only explicitly designated development tapes accepted')
    intervals=replay_dataset._intervals(header)
    if any(b>=cutoff_ms for a,b in intervals):
        raise ValueError('recording crosses the registered development boundary')
    return header


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',nargs='+',required=True)
    p.add_argument('--kind',choices=['raw','normalized'],default='normalized')
    p.add_argument('--batch-dir',required=True)
    p.add_argument('--development-end-before',required=True,
                   help='exclusive ISO UTC recording boundary; no old OOS inheritance')
    replay_lab.add_run_options(p,with_cache=False)
    a=p.parse_args(argv)
    cutoff=datetime.fromisoformat(a.development_end_before.replace('Z','+00:00'))
    if cutoff.tzinfo is None:raise ValueError('explicit UTC/offset required')
    cutoff_ms=cutoff.timestamp()*1000
    root=Path(a.batch_dir).resolve();root.mkdir(parents=True,exist_ok=True)
    state={'schema':'historical-replay-batch-v1','status':'importing',
           'started_at_utc':datetime.now(timezone.utc).isoformat(),
           'development_end_before':cutoff.isoformat(),'inputs':[],
           'formal_oos_result':None,'full_days_claimed':False}
    started=time.perf_counter();paths=[]
    for name in a.input:
        row={'input':str(Path(name).resolve()),'status':'pending','pnl':None}
        state['inputs'].append(row);dump(root/'batch_manifest.json',state)
        try:
            if a.kind=='raw':
                item=replay_raw.prepare_raw(name,root/'raw',allow_unresolved=True)
                path=item['normalized_path'];row['raw_cache_hit']=item['cache_hit']
            else:path=name
            header=inspect_boundary(path,cutoff_ms)
            row.update(status='admitted',normalized_path=str(Path(path).resolve()),
                       normalized_sha256=replay_lab.sha(path),dataset_id=header['dataset_id'],
                       observed_hours=header.get('observed_hours'),
                       execution_book_sides=header.get('execution_book_sides'))
            paths.append(path)
        except Exception as exc:
            row.update(status='blocked',error=str(exc))
        dump(root/'batch_manifest.json',state)
    if not paths:
        state.update(status='blocked_no_admitted_inputs',new_backtests=0)
        dump(root/'batch_manifest.json',state);print(json.dumps(state));return 2
    try:
        data=replay_dataset.ingest(paths,root/'datasets')
        state.update(status='replaying',cache_path=data['cache_path'],
                     dataset_key=data['dataset_key'],coverage=data['coverage'])
        dump(root/'batch_manifest.json',state)
        result=replay_lab.run_cached(a,data['cache_path'])
        state.update(status='completed_partial_inputs' if any(r['status']=='blocked' for r in state['inputs']) else 'completed',
                     replay=result,elapsed_seconds=time.perf_counter()-started)
        dump(root/'batch_manifest.json',state)
        print(json.dumps({'status':state['status'],'run_path':result['run_path'],
                          'batch_manifest':str(root/'batch_manifest.json'),
                          'observed_hours':data['coverage']['observed_hours'],
                          'registered_markets':data['coverage']['registered_markets'],
                          'elapsed_seconds':state['elapsed_seconds']}),flush=True)
        return 0
    except Exception as exc:
        state.update(status='blocked',error=str(exc),pnl=None)
        dump(root/'batch_manifest.json',state);raise


if __name__=='__main__':raise SystemExit(main())
