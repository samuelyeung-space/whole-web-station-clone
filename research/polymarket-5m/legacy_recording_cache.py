"""Content-addressed, lossless BTC legacy recording ingestion; never a PnL.

Raw payloads and missing fields are preserved. No venue-time alignment, label
availability backfill, direction inference, sequence repair or health inference.
This cache is an ingestion layer, NOT a strict-execution adapter. Each stream
retains file order, including regressions, for explicit downstream handling.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import tarfile
import tempfile
import time
import zipfile

SCHEMA = 'btc5m-legacy-lossless-cache-v1'

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def clock(row):
    # Only explicit local receipt fields. updatedAt, closedTime and source_ts
    # are deliberately not candidates for local information availability.
    for key, scale in [('recv_ms', 1), ('receive_ts', 1000), ('_recorded_at_ms', 1)]:
        if row.get(key) not in (None, ''):
            value = row[key]
            if isinstance(value, bool):
                raise ValueError('boolean receipt clock')
            value = float(value) * scale
            if not math.isfinite(value) or value <= 0:
                raise ValueError('invalid receipt clock')
            return value, key
    return None, None

def classify(name):
    if not name.startswith('bundle-btc/'):
        return None
    if '/book/BTC-5m/' in name: return 'token_snapshot'
    if '/best_bid_ask/BTC-5m/' in name: return 'token_bbo'
    if '/last_trade_price/BTC-5m/' in name: return 'token_trade'
    if '/markets/BTC-5m/' in name: return 'market_metadata'
    if name.endswith('/binance_trades.jsonl.gz'): return 'btc_spot_trade'
    if name.endswith('/coinbase_trades.jsonl.gz'): return 'coinbase_trade'
    if 'poly_probability_observations' in name: return 'probability_observation'
    if name.endswith('.market_outcomes.csv.gz'): return 'final_label'
    if name.endswith('.market_registry.csv.gz'): return 'market_registry'
    return 'other_btc_evidence'

def envelope(row, kind, member, ordinal):
    now, field = clock(row)
    return dict(kind=kind, recv_ms=now, receipt_field=field,
                member=member, member_row=ordinal, raw=row)

def verify(directory, identity):
    manifest = json.loads((directory/'manifest.json').read_text())
    if manifest['identity'] != identity or manifest['schema'] != SCHEMA:
        raise ValueError('cache identity mismatch')
    for stream in manifest['streams']:
        p = directory / stream['cache_file']
        if not p.is_file() or sha(p) != stream['sha256']:
            raise ValueError('cache content failed verification')
    return manifest

def ingest(path, cache):
    start = time.perf_counter()
    path, cache = Path(path), Path(cache)
    identity = dict(archive_sha256=sha(path), code_sha256=sha(__file__), schema=SCHEMA)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    destination = cache/key
    if destination.exists():
        result = verify(destination, identity)
        return dict(cache_key=key, reused=True, elapsed_seconds=time.perf_counter()-start,
                    manifest=result)
    cache.mkdir(parents=True, exist_ok=True)
    # Publish only after every selected compressed member and output hash passes.
    stage = Path(tempfile.mkdtemp(prefix='pending-', dir=cache))
    streams, market_rows = [], []
    with zipfile.ZipFile(path) as z:
        if z.namelist() != ['raw.tgz']:
            raise ValueError('unsupported outer archive layout')
        with z.open('raw.tgz') as outer:
            with tarfile.open(fileobj=outer, mode='r|gz') as archive:
                for member in archive:
                    kind = classify(member.name)
                    if not member.isfile() or kind is None:
                        continue
                    if not member.name.endswith(('.jsonl.gz', '.csv.gz')):
                        raise ValueError('unsupported BTC member')
                    output = stage/f'{len(streams):03d}-{kind}.jsonl.gz'
                    fields, missing = Counter(), Counter()
                    count = regressions = 0
                    first = last = previous = None
                    slots = Counter()
                    with gzip.GzipFile(fileobj=archive.extractfile(member)) as compressed:
                        with io.TextIOWrapper(compressed, encoding='utf-8') as src, gzip.open(output, 'wt') as dst:
                            rows = (json.loads(line) for line in src) if member.name.endswith('.jsonl.gz') else csv.DictReader(src)
                            for count, row in enumerate(rows, 1):
                                event = envelope(row, kind, member.name, count)
                                now = event['recv_ms']
                                fields.update(row.keys())
                                if now is not None:
                                    first = now if first is None else min(first, now)
                                    last = now if last is None else max(last, now)
                                    regressions += previous is not None and now < previous
                                    previous = now
                                    slots[str(int(now//300000)*300)] += 1
                                else: missing['local_receipt_clock'] += 1
                                if kind == 'btc_spot_trade':
                                    for field in ['size', 'aggressor_side', 'buyer_is_maker', 'connection_epoch', 'stream_sequence']:
                                        if row.get(field) is None: missing[field] += 1
                                if kind == 'market_metadata':
                                    market_rows.append(dict(slug=row.get('slug'), start_sec=row.get('start_sec'),
                                        end_sec=row.get('end_sec'), local_metadata_recv_ms=now,
                                        resolved=row.get('resolved'),
                                        settlement_known_ms=row.get('settlement_known_ms')))
                                dst.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
                    streams.append(dict(member=member.name, kind=kind, cache_file=output.name,
                        sha256=sha(output), rows=count, recv_min_ms=first, recv_max_ms=last,
                        receipt_regressions=regressions, field_counts=dict(fields),
                        missing_counts=dict(missing), receipt_slot_row_counts=dict(slots)))
            # Drain outer ZIP member to trigger its CRC validation.
            while outer.read(1 << 20): pass
    if not streams: raise ValueError('no BTC evidence found')
    manifest = dict(schema=SCHEMA, identity=identity, streams=streams, markets=market_rows,
        total_rows=sum(s['rows'] for s in streams),
        strict_execution_ready=False, formal_oos_result=None, pnl=None,
        blockers=['legacy streams require explicit downstream schema admission',
                  'unclocked metadata/labels must not be assigned historical local availability',
                  'source connection continuity is not established by first/last timestamps'],
        semantics={'receipt_slot_counts':'observed rows, not continuous healthy coverage',
                   'raw_payload':'preserved without imputation',
                   'unclocked_rows':'retained in separate member streams, never sorted into causal replay',
                   'split':'historical development only; no untouched OOS claim'})
    (stage/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    stage.rename(destination)
    return dict(cache_key=key, reused=False, elapsed_seconds=time.perf_counter()-start, manifest=manifest)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', nargs='+', required=True)
    p.add_argument('--cache', required=True)
    p.add_argument('--index', required=True)
    args = p.parse_args()
    index = Path(args.index)
    entries = json.loads(index.read_text()) if index.exists() else {}
    for path in args.input:
        result = ingest(path, args.cache)
        entries[result['cache_key']] = dict(archive_name=Path(path).name,
            cache_path=str(Path(args.cache).resolve()/result['cache_key']),
            total_rows=result['manifest']['total_rows'], strict_execution_ready=False)
        temporary = index.with_suffix(index.suffix+'.pending')
        temporary.write_text(json.dumps(entries, indent=2)+'\n')
        temporary.replace(index)
        print(json.dumps({k:v for k,v in result.items() if k != 'manifest'}))

if __name__ == '__main__': main()
