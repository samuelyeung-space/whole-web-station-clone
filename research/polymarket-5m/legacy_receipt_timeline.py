"""Offline receipt-indexed legacy cache. No execution, orders or label imputation.

Build once; query historical state strictly BEFORE a decision. Events sharing a
receipt timestamp are an unordered batch, never a fabricated transport sequence.
Unclocked records are retained but excluded from timeline/as-of queries.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import time

from legacy_recording_cache import sha, clock

SCHEMA = 'btc5m-legacy-receipt-index-v1'

def entity(event):
    raw = event['raw']
    return str(raw.get('asset_id') or raw.get('market_id') or raw.get('slug') or '')

def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite numeric timestamp required')
    return value

def build(cache_path, output_root):
    started = time.perf_counter()
    cache, root = Path(cache_path), Path(output_root)
    manifest_path = cache/'manifest.json'
    original = json.loads(manifest_path.read_text())
    if original['schema'] != 'btc5m-legacy-lossless-cache-v1':
        raise ValueError('unsupported input schema')
    identity = dict(input_manifest_sha256=sha(manifest_path), code_sha256=sha(__file__),
                    clock_code_sha256=sha(Path(__file__).with_name('legacy_recording_cache.py')))
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    dest = root/key
    if dest.exists():
        result = json.loads((dest/'manifest.json').read_text())
        if result['identity'] != identity or sha(dest/'timeline.sqlite') != result['database_sha256']:
            raise ValueError('timeline integrity mismatch')
        return dict(cache_path=str(dest), reused=True, elapsed_seconds=time.perf_counter()-started, manifest=result)
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='pending-', dir=root))
    db = sqlite3.connect(stage/'timeline.sqlite')
    db.executescript('''CREATE TABLE events (
        stream INTEGER NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL,
        entity TEXT NOT NULL, recv REAL, envelope TEXT NOT NULL,
        PRIMARY KEY(stream, ordinal));''')
    streams = []
    for idx, stream in enumerate(original['streams']):
        path = cache/stream['cache_file']
        if path.parent.resolve() != cache.resolve() or sha(path) != stream['sha256']:
            raise ValueError('input stream identity mismatch')
        rows = missing = global_regressions = entity_regressions = 0
        previous = None
        per_entity = {}
        batch = []
        with gzip.open(path, 'rt') as source:
            for rows, line in enumerate(source, 1):
                event = json.loads(line)
                now, field = clock(event['raw'])
                if (event['member_row'] != rows or event['member'] != stream['member']
                    or event['kind'] != stream['kind'] or event['recv_ms'] != now
                    or event['receipt_field'] != field):
                    raise ValueError('envelope/receipt provenance mismatch')
                who = entity(event)
                if now is None:
                    missing += 1
                else:
                    global_regressions += previous is not None and now < previous
                    entity_regressions += who in per_entity and now < per_entity[who]
                    previous = per_entity[who] = now
                batch.append((idx, rows, event['kind'], who, now, line.strip()))
                if len(batch) == 5000:
                    db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?)', batch)
                    batch.clear()
        db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?)', batch)
        if rows != stream['rows']:
            raise ValueError('stream row-count mismatch')
        streams.append(dict(kind=stream['kind'], rows=rows, missing_receipt_rows=missing,
                            file_regressions=global_regressions, within_entity_regressions=entity_regressions))
    db.executescript('''CREATE INDEX chronological ON events(recv,stream,ordinal) WHERE recv IS NOT NULL;
        CREATE INDEX asof_index ON events(kind,entity,recv) WHERE recv IS NOT NULL;''')
    db.commit()
    count, clocked, low, high = db.execute('SELECT COUNT(*),COUNT(recv),MIN(recv),MAX(recv) FROM events').fetchone()
    if count != original['total_rows']:
        raise ValueError('input total mismatch')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('sqlite integrity failed')
    db.close()
    result = dict(schema=SCHEMA, identity=identity, total_rows=count, clocked_rows=clocked,
        unclocked_rows=count-clocked, recv_min_ms=low, recv_max_ms=high, streams=streams,
        database_sha256=sha(stage/'timeline.sqlite'), strict_execution_ready=False, pnl=None,
        formal_oos_result=None, split='historical_development',
        policy={'asof':'strict recv_ms < decision_ms; no nearest/forward interpolation',
                'ties':'unordered batch; conflicting latest raw payloads fail closed',
                'missing_clocks':'excluded from timeline; retained in evidence table',
                'health':'no continuity or execution-health inferred from ordering',
                'labels':'no final-label/metadata availability or cash-release time imputed'})
    (stage/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    stage.rename(dest)
    return dict(cache_path=str(dest), reused=False, elapsed_seconds=time.perf_counter()-started, manifest=result)

class Timeline:
    def __init__(self, directory):
        root = Path(directory)
        self.manifest = json.loads((root/'manifest.json').read_text())
        if self.manifest['schema'] != SCHEMA or sha(root/'timeline.sqlite') != self.manifest['database_sha256']:
            raise ValueError('timeline integrity mismatch')
        self.db = sqlite3.connect((root/'timeline.sqlite').resolve().as_uri()+'?mode=ro', uri=True)

    def close(self):
        self.db.close()

    def batches(self, start_ms, end_ms, kinds):
        """Half-open interval, explicit kind whitelist; provenance order is not causal order."""
        number(start_ms); number(end_ms)
        if start_ms >= end_ms or not kinds: raise ValueError('invalid range or empty kinds')
        marks = ','.join('?' for _ in kinds)
        rows = self.db.execute(f'SELECT recv,envelope FROM events WHERE recv>=? AND recv<? AND kind IN ({marks}) ORDER BY recv,stream,ordinal', (start_ms,end_ms,*kinds))
        for now, group in itertools.groupby(rows, key=lambda r:r[0]):
            yield now, [json.loads(row[1]) for row in group]

    def asof(self, kind, who, decision_ms, max_age_ms):
        number(decision_ms); number(max_age_ms)
        if max_age_ms < 0: raise ValueError('negative age')
        latest = self.db.execute('SELECT MAX(recv) FROM events WHERE kind=? AND entity=? AND recv<?',
                                 (kind,str(who),decision_ms)).fetchone()[0]
        if latest is None: return dict(status='missing', event=None)
        if decision_ms-latest > max_age_ms: return dict(status='stale', event=None, recv_ms=latest)
        events = [json.loads(r[0]) for r in self.db.execute('SELECT envelope FROM events WHERE kind=? AND entity=? AND recv=? ORDER BY stream,ordinal', (kind,str(who),latest))]
        payloads = {json.dumps(e['raw'], sort_keys=True, separators=(',',':')) for e in events}
        if len(payloads) != 1:
            return dict(status='ambiguous_equal_receipt', event=None, recv_ms=latest, rows=len(events))
        return dict(status='available', event=events[0], duplicate_rows=len(events)-1)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-cache', required=True)
    p.add_argument('--output-root', required=True)
    args = p.parse_args()
    result = build(args.input_cache, args.output_root)
    print(json.dumps(result))

if __name__ == '__main__': main()
