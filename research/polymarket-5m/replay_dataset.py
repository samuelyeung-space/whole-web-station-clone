"""Immutable, receipt-clock normalized tape admission for development replay.

Import never downloads, infers aggressor side, fills gaps, or upgrades OOS status.
Coverage means declared recording span only; it is not feed-health certification.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

SCHEMA_VERSION = 'replay_dataset_v1'
DAY_MS = 86400000
SLOT_MS = 300000
OFFICIAL = {'polymarket', 'gamma', 'chainlink'}
KINDS = {'dataset', 'market', 'constraint', 'book', 'decision', 'outcome'}


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _json(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def _records(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('event must be an object')
                _json(event)  # Reject nonstandard NaN/Infinity rather than silently admitting it.
            except (ValueError, TypeError) as exc:
                raise ValueError(f'{path}: line {n}: invalid event: {exc}') from exc
            yield event


def _intervals(header):
    value = header.get('observed_intervals_ms')
    if value is None:
        value = [[header.get('coverage_start_ms'), header.get('coverage_end_ms')]]
    if not isinstance(value, list) or not value:
        raise ValueError('explicit observed recording intervals required')
    result = []
    for pair in value:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(_number(v) for v in pair) or pair[0] >= pair[1]):
            raise ValueError('invalid observed_intervals_ms; do not infer coverage from sparse events')
        if result and pair[0] <= result[-1][1]:
            raise ValueError('observed_intervals_ms must be ordered and nonoverlapping')
        result.append(list(pair))
    return result


def _in_span(intervals, at):
    return any(a <= at <= b for a, b in intervals)


def _check_event(event, header, markets, outcomes, previous):
    kind, now = event.get('type'), event.get('recv_ms')
    if kind not in KINDS:
        raise ValueError(f'unknown record type {kind!r}')
    if not _number(now) or now < 0:
        raise ValueError('every record needs finite nonnegative local recv_ms')
    if previous is not None and now < previous:
        raise ValueError('input must be sorted by local recv_ms')
    if kind == 'dataset':
        if header is not None or previous is not None:
            raise ValueError('one dataset header must be the first record')
        if not isinstance(event.get('dataset_id'), str) or not event['dataset_id']:
            raise ValueError('dataset_id required')
        if not isinstance(event.get('split'), str) or not event['split']:
            raise ValueError('explicit split required')
        if not isinstance(event.get('provenance'), dict) or not event['provenance'].get('source'):
            raise ValueError('provenance.source required')
        _intervals(event)
        if event.get('book_resolution') == 'decision_and_match_projection':
            if type(event.get('decision_grid_ms')) is not int or event['decision_grid_ms'] <= 0:
                raise ValueError('projected tape needs positive decision_grid_ms')
            if (not isinstance(event.get('supported_latencies_ms'), list)
                    or not event['supported_latencies_ms']
                    or any(type(v) is not int or v <= 0 for v in event['supported_latencies_ms'])):
                raise ValueError('projected tape needs supported_latencies_ms')
        return
    if header is None:
        raise ValueError('dataset header must precede events')
    mid = event.get('market_id')
    if not isinstance(mid, str) or not mid:
        raise ValueError('market_id required')
    if kind == 'market':
        a, b = event.get('start_ms'), event.get('end_ms')
        if not _number(a) or not _number(b) or b - a != SLOT_MS:
            raise ValueError('market must be exactly five minutes')
        if event.get('label_source') not in OFFICIAL:
            raise ValueError('official market label_source required')
        definition = (a, b, event['label_source'])
        if mid in markets and markets[mid] != definition:
            raise ValueError('conflicting market definition')
        markets[mid] = definition
        return
    if mid not in markets:
        raise ValueError('market event before locally received market registration')
    if kind in {'book', 'decision'} and not _in_span(_intervals(header), now):
        raise ValueError('book or decision outside declared recording intervals')
    if kind == 'book':
        for side in ('up', 'down'):
            at = event.get(side + '_recv_ms', now)
            if _number(at) and at > now:
                raise ValueError('book side cannot use a future local receipt')
    elif kind == 'decision':
        features, available = event.get('features'), event.get('feature_available_ms')
        # Retain missing/noncausal feature values unchanged. The execution engine
        # rejects only candidates requiring those fields, and counts the rejection.
        if header.get('book_resolution') == 'decision_and_match_projection':
            grid = header['decision_grid_ms']
            if abs(now / grid - round(now / grid)) > 1e-6:
                raise ValueError('decision outside registered projection grid')
    elif kind == 'constraint':
        names = ('min_order_size', 'up_tick_size', 'down_tick_size')
        if not any(name in event for name in names):
            raise ValueError('constraint must contain a market constraint')
        for name in names:
            if name not in event:
                continue
            # Explicit null invalidates an earlier constraint (for example on
            # reconnect). Preserve it: the execution engine must fail closed
            # until a later causal valid observation restores this field.
            if event[name] is not None and (not _number(event[name]) or event[name] <= 0):
                raise ValueError('constraints must be finite and positive')
            at = event.get(name + '_available_ms', now)
            if not _number(at) or at > now:
                raise ValueError('constraint future availability')
    elif kind == 'outcome':
        resolved = event.get('resolved_ms')
        if (event.get('winner') not in ('Up', 'Down') or event.get('source') != markets[mid][2]
                or not _number(resolved) or not markets[mid][1] <= resolved <= now):
            raise ValueError('invalid official outcome or noncausal resolution timestamp')
        if mid in outcomes and outcomes[mid] != event['winner']:
            raise ValueError('conflicting official outcomes')
        outcomes[mid] = event['winner']


def _scan(path):
    digest, counts, issues = hashlib.sha256(), Counter(), Counter()
    header, previous = None, None
    markets, outcomes = {}, {}
    market_ids = {kind: set() for kind in ('market', 'book', 'decision', 'outcome')}
    daily_ids = defaultdict(lambda: defaultdict(set))
    for event in _records(path):
        _check_event(event, header, markets, outcomes, previous)
        now, kind = event['recv_ms'], event['type']
        if kind == 'dataset':
            header = event
        digest.update((_json(event) + '\n').encode())
        counts[kind] += 1
        if kind == 'decision':
            features, available = event.get('features'), event.get('feature_available_ms')
            if not isinstance(features, dict) or not isinstance(available, dict):
                issues['decision_missing_feature_maps'] += 1
            else:
                for name, value in features.items():
                    if value is None:
                        issues['null_feature:' + name] += 1
                    elif not _number(value):
                        issues['invalid_feature:' + name] += 1
                    elif not _number(available.get(name)):
                        issues['missing_feature_availability:' + name] += 1
                    elif available[name] > now:
                        issues['future_feature:' + name] += 1
        elif kind == 'constraint':
            for name in ('min_order_size', 'up_tick_size', 'down_tick_size'):
                if name in event and event[name] is None:
                    issues['null_constraint:' + name] += 1
        elif kind == 'book':
            for side in ('up','down'):
                if not _number(event.get(side+'_recv_ms',now)):
                    issues['missing_or_invalid_' + side + '_receipt'] += 1
            for name in ('up_asks', 'down_asks'):
                if not isinstance(event.get(name), list):
                    issues['missing_or_invalid_' + name] += 1
            if type(event.get('healthy')) is not bool:
                issues['missing_or_invalid_book_health'] += 1
            if not _number(event.get('fee_rate')) or not 0 <= event['fee_rate'] <= 1:
                issues['missing_or_invalid_fee'] += 1
        previous = now
        if kind in market_ids:
            mid = event['market_id']
            market_ids[kind].add(mid)
            # Attribute market counts to market start date; receipt day is distinct.
            day = int(markets[mid][0] // DAY_MS)
            daily_ids[day][kind].add(mid)
    if header is None:
        raise ValueError('empty dataset')
    return {'path': str(Path(path).resolve()), 'file_sha256': _sha(path),
            'event_sha256': digest.hexdigest(), 'header': header, 'counts': dict(counts),
            'markets': markets, 'outcomes': outcomes, 'market_ids': market_ids, 'quality_issues': dict(issues),
            'daily_ids': daily_ids, 'intervals': _intervals(header)}


def _coverage(scans, intervals):
    days = set()
    market_sets = {kind: set() for kind in ('market', 'book', 'decision', 'outcome')}
    daily_ids = defaultdict(lambda: defaultdict(set))
    days.update(range(int(intervals[0][0] // DAY_MS), int(math.ceil(intervals[-1][1] / DAY_MS))))
    for scan in scans:
        for kind, ids in scan['market_ids'].items():
            market_sets[kind].update(ids)
        for day, kinds in scan['daily_ids'].items():
            for kind, ids in kinds.items():
                daily_ids[day][kind].update(ids)
    all_market_definitions = {}
    for scan in scans:
        all_market_definitions.update(scan['markets'])
    daily = []
    for day in sorted(days | set(daily_ids)):
        a, b = day * DAY_MS, (day + 1) * DAY_MS
        segments = [[max(a, x), min(b, y)] for x, y in intervals if max(a, x) < min(b, y)]
        recorded, complete = [], []
        for slot in range(288):
            start, end = a + slot * SLOT_MS, a + (slot + 1) * SLOT_MS
            if any(x < end and y > start for x, y in segments):
                recorded.append(slot)
            if any(x <= start and y >= end for x, y in segments):
                complete.append(slot)
        cursor, gaps = a, []
        for x, y in segments:
            if x > cursor:
                gaps.append([cursor, x])
            cursor = max(cursor, y)
        if cursor < b:
            gaps.append([cursor, b])
        present_ids = set.intersection(*(set(daily_ids[day][kind])
                                        for kind in ('market','book','decision','outcome')))
        present_slots = {int(all_market_definitions[mid][0] // SLOT_MS)
                         for mid in present_ids if all_market_definitions[mid][0] % SLOT_MS == 0}
        daily.append({'data_present_slots':len(present_slots), 'date_utc': datetime.fromtimestamp(a / 1000, timezone.utc).date().isoformat(),
                      'expected_slots': 288, 'recorded_span_slots': len(recorded),
                      'complete_span_slots': len(complete), 'missing_span_slots': 288 - len(recorded),
                      'observed_seconds': sum(y-x for x,y in segments)/1000,
                      'gaps_ms': gaps,
                      'registered_markets': len(daily_ids[day]['market']),
                      'decision_markets': len(daily_ids[day]['decision']),
                      'book_markets': len(daily_ids[day]['book']),
                      'outcome_markets': len(daily_ids[day]['outcome'])})
    all_markets = {}
    for scan in scans:
        all_markets.update(scan['markets'])
    return {'markets':[{'market_id':mid, 'start_ms':v[0], 'end_ms':v[1], 'label_source':v[2]}
                       for mid,v in sorted(all_markets.items())],
            'basis': 'UTC five-minute clock slots; declared recording span, never feed health',
            'market_date_basis': 'market start UTC date, not receipt day',
            'data_present_slots_basis': 'distinct aligned UTC five-minute slots with a common market_id present in registration, book, decision and official outcome events; presence is not health',
            'expected_slots_per_utc_day': 288, 'expected_slots_per_7_days': 2016,
            'observed_intervals_ms': intervals,
            'observed_hours': sum(b-a for a,b in intervals)/3600000,
            'inter_recording_gaps_ms': [[intervals[i-1][1], intervals[i][0]] for i in range(1,len(intervals))],
            'registered_markets': len(market_sets['market']), 'decision_markets': len(market_sets['decision']),
            'book_markets': len(market_sets['book']), 'outcome_markets': len(market_sets['outcome']),
            'daily': daily, 'complete_feed_health_verified': False}


def _body(path):
    events = _records(path)
    next(events)
    return events


def verify_cache(cache_dir):
    """Verify immutable manifest identity and tape bytes; return the manifest."""
    root = Path(cache_dir)
    manifest = json.loads((root / 'manifest.json').read_text())
    signature = manifest.get('manifest_sha256')
    body = {k:v for k,v in manifest.items() if k != 'manifest_sha256'}
    if signature != hashlib.sha256(_json(body).encode()).hexdigest():
        raise ValueError('cache manifest corrupted')
    identity_hash = hashlib.sha256(_json(manifest.get('identity')).encode()).hexdigest()
    if (manifest.get('schema_version') != SCHEMA_VERSION or manifest.get('dataset_key') != root.name
            or identity_hash != root.name):
        raise ValueError('cache identity mismatch')
    tape = root / 'events.jsonl.gz'
    if (tape.stat().st_size != manifest['tape_bytes'] or _sha(tape) != manifest['tape_sha256']):
        raise ValueError('cached tape corrupted; existing cache will not be overwritten')
    return manifest


def iter_events(cache_dir):
    """Hash-verify the immutable canonical tape before yielding any event.

    Ingest already parsed, validated and serialized every record with finite
    JSON checks. Repeating that serialization on every replay only burns CPU;
    verified unchanged cache bytes can be decoded directly. External inputs
    still go through _records/_scan and retain all admission checks.
    """
    verify_cache(cache_dir)
    with gzip.open(Path(cache_dir) / 'events.jsonl.gz', 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def ingest(paths, cache_root):
    """Admit one/many normalized tapes and return manifest + cache_path/cache_hit.

    Exact logical event streams are deduplicated even across gzip encodings.
    Different tapes with intersecting observation spans are rejected: their
    independent first-signal and depth histories must not be double counted.
    Mixed split or incompatible projection semantics are rejected explicitly.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = list(paths)
    if not paths:
        raise ValueError('at least one normalized tape is required')
    unique = {}
    for path in paths:
        scan = _scan(path)
        unique.setdefault(scan['event_sha256'], scan)
    scans = [unique[k] for k in sorted(unique)]
    splits = {s['header']['split'] for s in scans}
    if len(splits) != 1:
        raise ValueError('mixed input split prohibited; ingest each research split separately')
    fields = ('book_resolution', 'decision_grid_ms', 'supported_latencies_ms',
              'execution_book_sides')
    for field in fields:
        values = {_json(s['header'].get(field)) for s in scans}
        if len(values) != 1:
            raise ValueError(f'incompatible input {field}; normalize separately')
    intervals = sorted(pair for s in scans for pair in s['intervals'])
    for i in range(1, len(intervals)):
        if intervals[i][0] <= intervals[i-1][1]:
            raise ValueError('different normalized tapes overlap observation spans; admission refused')
    markets, outcomes = {}, {}
    for scan in scans:
        for target, source, label in ((markets,scan['markets'],'market definition'),(outcomes,scan['outcomes'],'outcome')):
            for mid,value in source.items():
                if mid in target and target[mid] != value:
                    raise ValueError('cross-tape conflicting ' + label)
                target[mid] = value
    identity = {'schema_version': SCHEMA_VERSION, 'adapter_code_sha256': _sha(Path(__file__)),
                'event_hashes': sorted(unique)}
    key = hashlib.sha256(_json(identity).encode()).hexdigest()
    cache_root = Path(cache_root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / key
    if target.exists():
        manifest = verify_cache(target)
        return {**manifest, 'cache_path': str(target), 'cache_hit': True,
                'input_files_requested': len(paths), 'duplicate_inputs_ignored': len(paths)-len(scans)}
    header = {'type':'dataset', 'recv_ms':min(s['header']['recv_ms'] for s in scans),
              'dataset_id':'replay-cache:' + key, 'split':next(iter(splits)),
              'provenance':{'source':'normalized_content_addressed_intake',
                            'source_headers':[s['header'] for s in scans],
                            'event_sha256':[s['event_sha256'] for s in scans]},
              'coverage_start_ms':intervals[0][0], 'coverage_end_ms':intervals[-1][1],
              'observed_intervals_ms':intervals,
              'observed_hours':sum(b-a for a,b in intervals)/3600000,
              'qualification':'ingestion does not establish OOS or execution validity'}
    for field in fields:
        if field in scans[0]['header']:
            header[field] = scans[0]['header'][field]
    stage = Path(tempfile.mkdtemp(prefix='.ingest-', dir=cache_root))
    try:
        tape = stage / 'events.jsonl.gz'
        count, event_digest = 0, hashlib.sha256()
        with open(tape, 'wb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='', compresslevel=1) as compressed:
                def write(event):
                    nonlocal count
                    data = (_json(event)+'\n').encode()
                    compressed.write(data)
                    event_digest.update(data)
                    count += 1
                write(header)
                # Stable merge preserves each input's equal-receipt event order.
                streams = [_body(s['path']) for s in scans]
                for event in heapq.merge(*streams, key=lambda e:e['recv_ms']):
                    write(event)
            raw.flush()
            os.fsync(raw.fileno())
        for source in scans:
            if _sha(source['path']) != source['file_sha256']:
                raise ValueError('input changed while being ingested; no cache committed')
        quality_issues = Counter()
        for source in scans:
            quality_issues.update(source['quality_issues'])
        manifest = {'quality_issues':dict(quality_issues), 'schema_version':SCHEMA_VERSION, 'dataset_key':key, 'identity':identity,
                    'split':header['split'], 'dataset_header':header,
                    'source_tapes':[{'event_sha256':s['event_sha256'], 'original_file_sha256':s['file_sha256'],
                                     'dataset_id':s['header']['dataset_id'], 'counts':s['counts']} for s in scans],
                    'source_tape_count':len(scans), 'record_count':count,
                    'tape_sha256':_sha(tape), 'tape_event_sha256':event_digest.hexdigest(),
                    'tape_bytes':tape.stat().st_size, 'coverage':_coverage(scans,intervals),
                    'new_backtests':0, 'pnl':None}
        manifest['manifest_sha256'] = hashlib.sha256(_json(manifest).encode()).hexdigest()
        (stage/'manifest.json').write_text(_json(manifest)+'\n')
        try:
            stage.rename(target)
        except FileExistsError:
            verify_cache(target)
            shutil.rmtree(stage)
        result = verify_cache(target)
        return {**result, 'cache_path':str(target), 'cache_hit':False,
                'input_files_requested':len(paths), 'duplicate_inputs_ignored':len(paths)-len(scans)}
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
