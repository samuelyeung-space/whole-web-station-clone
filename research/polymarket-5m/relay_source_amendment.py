"""Immutable, deterministic source-label repair; zero new strategy evaluations.

Original recorded BINANCE_AGG_TRADE labels came from Polymarket RTDS
crypto_prices relay. Keep numeric events, clocks and original numeric producer
hashes unchanged, and attach the independently inspected recorder evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path

SOURCE = 'polymarket_rtds_binance_relay'
FIELDS = ('source_reference', 'source_id', 'source_topic')


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f, 'sha256').hexdigest()


def encoded(row):
    return json.dumps(row,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()+b'\n'


def repair(input_path, output_path, *, recorder_source, recorder_commit, recorder_sha256):
    src,out,recorder=map(Path,(input_path,output_path,recorder_source))
    if out.exists():raise ValueError('immutable repaired output already exists')
    if sha(recorder)!=recorder_sha256 or len(recorder_commit)!=40:
        raise ValueError('recorder evidence identity mismatch')
    recorder_bytes=recorder.read_bytes()
    if b'crypto_prices' not in recorder_bytes or b'BINANCE_AGG_TRADE' not in recorder_bytes:
        raise ValueError('expected inspected relay export source absent')
    mp=Path(str(src)+'.manifest.json');manifest=json.loads(mp.read_text())
    input_sha=sha(src)
    if manifest['output_sha256']!=input_sha:raise ValueError('original tape hash mismatch')
    identity={'schema':'relay-source-amendment-v1','input_sha256':input_sha,
        'input_manifest_sha256':sha(mp),'numeric_producer_identity':manifest['identity'],
        'amendment_code_sha256':sha(__file__),'recorder_commit':recorder_commit,
        'recorder_source_sha256':recorder_sha256,'source_reference':SOURCE,
        'source_topic':'crypto_prices','derived_same_events_zero_new_strategy_trials':True}
    counts=Counter();body_original=hashlib.sha256();body_repaired=hashlib.sha256()
    out.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation prevents a second amendment worker from sharing output.
    pending=out.with_name(out.name+'.amend-pending')
    with gzip.open(src,'rt') as f, pending.open('xb') as raw:
        old_header=json.loads(next(f))
        if old_header!=manifest['header'] or old_header['type']!='dataset':
            raise ValueError('original completed header mismatch')
        header=copy.deepcopy(old_header)
        header['dataset_id']='relay-source-v1:'+hashlib.sha256(encoded(identity)).hexdigest()[:20]
        header.update(source_reference=SOURCE,source_id=SOURCE,source_topic='crypto_prices',
            source_reference_semantics='Polymarket RTDS relay price updates; original local receive_ts retained; not direct Binance aggTrades, no aggressor side or volume')
        header['provenance']['source_identity_amendment']=identity
        header['assumptions']['binance_input']='original RTDS crypto_prices relay rows mislabeled BINANCE_AGG_TRADE by recorder; genuine local receipt clocks; sparse price-only feed'
        header['limitations']+=['source identity corrected from independently inspected recorder code; separate relay lane, never direct Binance or aggressor-volume evidence']
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=1) as dst:
            dst.write(encoded(header));counts['dataset']+=1
            for line in f:
                old=json.loads(line);new=copy.deepcopy(old)
                if new['type'] in ('market','decision'):
                    new.update(source_reference=SOURCE,source_id=SOURCE,source_topic='crypto_prices')
                a={k:v for k,v in old.items() if k not in FIELDS}
                b={k:v for k,v in new.items() if k not in FIELDS}
                if a!=b:raise ValueError('numeric/body event changed during source repair')
                body_original.update(encoded(a));body_repaired.update(encoded(b))
                dst.write(encoded(new));counts[new['type']]+=1
    if dict(counts)!=manifest['counts'] or body_original.digest()!=body_repaired.digest():
        raise ValueError('event preservation verification failed')
    if sha(src)!=input_sha:raise ValueError('source changed while repairing')
    if out.exists():raise ValueError('immutable output appeared during repair')
    pending.rename(out)
    result={**manifest,'identity':identity,'header':header,'output':str(out.resolve()),
        'output_sha256':sha(out),'source_identity_revision':identity,
        'original_header':old_header,'original_numeric_event_stream_sha256':body_original.hexdigest(),
        'corrected_numeric_event_stream_sha256':body_repaired.hexdigest(),
        'derived_same_events_zero_new_strategy_trials':True,'new_strategy_backtests':0,
        'new_strategy_trials':0,'formal_oos_result':None,'pnl':None}
    Path(str(out)+'.manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('input','out','recorder-source','recorder-commit','recorder-sha256'):p.add_argument('--'+n,required=True)
    a=p.parse_args();r=repair(a.input,a.out,recorder_source=a.recorder_source,
        recorder_commit=a.recorder_commit,recorder_sha256=a.recorder_sha256)
    print(json.dumps({k:r[k] for k in ('output','output_sha256','derived_same_events_zero_new_strategy_trials')},indent=2))
