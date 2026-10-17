"""Offline capture integrity/schema audit without prices, signals or PnL.

This is NOT replay admission. Metadata inspection cannot certify feed health,
official outcomes, complete days, queue fills or an untouched OOS protocol.
Raw trade maker flags are counted only for presence/type, never signed flow.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

DAY = 86400000


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def finite(value):
    import math
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def audit(root):
    root = Path(root).resolve()
    manifest = json.loads((root/'capture-manifest.json').read_text())
    if manifest.get('schema') != 'capture-checkpoint-v1':
        raise ValueError('unknown checkpoint schema')
    rows = manifest.get('files')
    if not isinstance(rows, list) or len({r['path'] for r in rows}) != len(rows):
        raise ValueError('invalid or duplicate checkpoint inventory')
    runtime = json.loads((root/'capture-runtime.json').read_text())
    a, b = runtime.get('started_ms'), runtime.get('ended_ms')
    if not finite(a) or not finite(b) or not 0 <= a < b:
        raise ValueError('invalid runtime receipt-clock span')
    writer = runtime.get('writer', {})
    writer_drained = (runtime.get('complete') is True and writer.get('error') is None
                      and writer.get('pending_bytes') == 0
                      and type(writer.get('accepted')) is int
                      and writer.get('accepted') == writer.get('written') == writer.get('flushed'))
    streams = []
    market_ids, market_rows = set(), 0
    errors, clob_states, lags, beats = Counter(), Counter(), [], []
    # Only these metadata streams are parsed. Decisions/quotes are CRC-scanned
    # as opaque bytes, never decoded into strategy or price observations.
    metadata = {'markets', 'errors', 'capture-health', 'binance',
                'binance-spot', 'binance-futures-trade'}
    for r in rows:
        relative = Path(r['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe inventory path')
        p = root/relative
        if p.is_symlink() or any(x.is_symlink() for x in p.parents if x != root) or not p.is_file():
            raise ValueError('nonregular checkpoint file')
        if p.stat().st_size != r['bytes'] or sha(p) != r['sha256']:
            raise ValueError('checkpoint member hash mismatch: '+r['path'])
        if not p.name.endswith('.jsonl.gz'):
            continue
        name = p.name[:-9]
        count, expanded, last = 0, 0, b''
        detail = {'path':r['path'], 'compressed_bytes':r['bytes'], 'sha256':r['sha256'],
                  'semantic_access':'metadata_only' if name in metadata else 'opaque_crc_only'}
        typed = Counter(); first = None; latest = None
        with gzip.open(p, 'rb') as f:
            if name in metadata:
                for line in f:
                    count += 1; expanded += len(line); last = line[-1:]
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('metadata row must be an object')
                    at = row.get('recv_ms', row.get('at', row.get('_recorded_at_ms')))
                    if finite(at):
                        first = at if first is None else min(first, at)
                        latest = at if latest is None else max(latest, at)
                    if name == 'markets':
                        slug = row.get('slug', '')
                        if re.fullmatch(r'btc-updown-5m-\d+', slug):
                            market_rows += 1; market_ids.add(slug)
                    elif name == 'errors':
                        errors[str(row.get('where', 'unknown'))] += 1
                    elif name == 'capture-health':
                        if finite(at): beats.append(at)
                        clob_states[str(row.get('clob_active'))] += 1
                        if finite(row.get('event_loop_lag_ms')): lags.append(row['event_loop_lag_ms'])
                    else:
                        typed['rows'] += 1
                        for key in ('recv_ms', 'T', 'p', 'q'):
                            if row.get(key) is not None: typed[key+'_present'] += 1
                        if type(row.get('m')) is bool: typed['maker_flag_boolean'] += 1
            else:
                for block in iter(lambda:f.read(1024**2), b''):
                    expanded += len(block); count += block.count(b'\n'); last = block[-1:]
        if expanded and last != b'\n':
            raise ValueError('unterminated JSONL stream: '+r['path'])
        detail.update(records=count, expanded_bytes=expanded, gzip_eof_crc_verified=True)
        if name in metadata:
            detail.update(first_local_ms=first, last_local_ms=latest)
        if typed: detail['field_presence_counts'] = dict(typed)
        streams.append(detail)
    daily = []
    for start in range(int(a//DAY)*DAY, int(b//DAY)*DAY+1, DAY):
        lo, hi = max(a,start), min(b,start+DAY)
        if hi <= lo: continue
        complete_slots = sum(lo <= t and hi >= t+300000 for t in range(start,start+DAY,300000))
        daily.append({'date_utc':datetime.fromtimestamp(start/1000,timezone.utc).date().isoformat(),
                      'declared_runtime_seconds':(hi-lo)/1000, 'complete_runtime_slots':complete_slots,
                      'full_runtime_day':lo==start and hi==start+DAY,
                      'full_feed_recorded_day_verified':False})
    beats.sort()
    return {'schema':'capture-quarantine-audit-v1', 'run_id':manifest.get('run_id'),
            'source_commit':manifest.get('source_commit'), 'runtime':runtime,
            'checkpoint_file_count':len(rows), 'gzip_streams':streams,
            'all_member_hashes_verified':True, 'all_gzip_eof_crc_verified':True,
            'writer_drained':writer_drained, 'btc5m_registered_markets':len(market_ids),
            'btc5m_registration_rows':market_rows, 'error_counts_by_where':dict(errors),
            'health':{'heartbeats':len(beats), 'clob_active_states':dict(clob_states),
                      'max_heartbeat_gap_ms':max((y-x for x,y in zip(beats,beats[1:])),default=None),
                      'max_event_loop_lag_ms':max(lags,default=None)}, 'daily_runtime':daily,
            'complete_feed_health_verified':False, 'full_recorded_utc_days':None,
            'execution_input_admitted':False, 'official_outcomes_audited':False,
            'quarantine_reason':'raw capture in protected Oct12-19 window; no strategy replay or fitting',
            'new_financial_replays':0, 'new_strategy_attempts':0,
            'seven_day_result':None, 'formal_oos_result':None,
            'limits':['CRC and writer drain are not feed completeness',
                      'runtime span is not per-market quote freshness or market coverage',
                      'field presence is not an executed causal feature or profitable candidate']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=Path(a.out)
    if out.exists():raise ValueError('refusing to overwrite prior audit')
    result=audit(a.root)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f:json.dump(result,f,sort_keys=True,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ('run_id','writer_drained','checkpoint_file_count',
                                         'btc5m_registered_markets','new_financial_replays')}))


if __name__=='__main__':main()
