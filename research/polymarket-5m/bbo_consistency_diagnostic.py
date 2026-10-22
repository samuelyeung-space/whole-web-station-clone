"""Bounded receipt-prefix book diagnostics. No financial result or orders."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict, deque
from itertools import groupby, islice
from pathlib import Path

import complement_queue_lag as lag


class BboAudit:
    def __init__(self, examples_per_state=3):
        if not 0 <= examples_per_state <= 20:
            raise ValueError('examples_per_state must be0..20')
        self.cap = examples_per_state
        self.counts = Counter()
        self.examples = defaultdict(list)

    def observe(self, check):
        bid, ask = check['bid_matches'], check['ask_matches']
        self.counts['checks'] += 1
        if bid and ask:
            self.counts['matched'] += 1
            return
        sides = 'both' if not bid and not ask else ('bid' if not bid else 'ask')
        prior = 'awaiting_snapshot' if check['snapshot_required_before_check'] else 'depth_available'
        key = prior + '/' + sides
        self.counts['mismatched'] += 1
        self.counts[key] += 1
        if len(self.examples[key]) < self.cap:
            self.examples[key].append(dict(check))

    def result(self):
        assert self.counts['checks'] == self.counts['matched'] + self.counts['mismatched']
        return dict(counts=dict(self.counts), examples=dict(self.examples))


def first_depth_witness(path, audit):
    candidates = [r for key, rows in audit.examples.items()
                  if key.startswith('depth_available/') for r in rows]
    if not candidates:
        return None
    first = min(candidates, key=lambda r: r['recv_ms'])
    target = first['recv_ms']
    rows = deque(maxlen=100)
    window_count = 0
    # A second bounded read captures provenance only, never a replacement book.
    with gzip.open(path, 'rt') as stream:
        for number, line in enumerate(stream, 1):
            event = json.loads(line)
            if event['recv_ms'] > target:
                break
            if event['recv_ms'] >= target - 20:
                window_count += 1
                rows.append(dict(input_line=number, event=event))
    return dict(check=first, lookback_ms=20, matching_raw_rows=window_count,
                retained_rows=list(rows), truncated=window_count > len(rows),
                interpretation='Receipt-ordered raw context; any absent deletion remains unknown. Do not synthesize it from reported BBO.')


def diagnose(archive, *, max_batches=50000, examples_per_state=3):
    if not 1 <= max_batches <= 1000000:
        raise ValueError('max_batches must be1..1000000')
    root = Path(archive)
    standard = root/'strict' if (root/'strict'/'manifest.json').is_file() else root
    manifest = json.loads((standard/'manifest.json').read_text())
    mappings = list(lag.archive.iter_market_mappings(standard/'market_registry.csv.gz'))
    prefix = dict(physical_batches=0, logical_events=0, started_ms=None, ended_ms=None)
    event_digest = hashlib.sha256()
    def events():
        grouped = groupby(lag.iter_physical_clob_events(standard/'clob_events.jsonl.gz'),
                          key=lambda r: r['physical_batch_id'])
        for _, batch in islice(grouped, max_batches):
            prefix['physical_batches'] += 1
            for row in batch:
                prefix['logical_events'] += 1
                t = row['recv_ms']
                prefix['started_ms'] = t if prefix['started_ms'] is None else prefix['started_ms']
                prefix['ended_ms'] = t
                event_digest.update((json.dumps(row, sort_keys=True, separators=(',', ':'),
                                               allow_nan=False) + '\n').encode())
                yield row
    audit = BboAudit(examples_per_state)
    # No labels are loaded. The execution detector is reused solely to observe
    # its own consistency checks. Any unresolved records are deliberately not
    # included as economic results, and the artificial prefix end is explicit.
    ignored_rows, counters = lag.replay(events(), mappings, {},
        segment_end_ms=float(manifest['ended_ms']), bbo_diagnostic_hook=audit.observe)
    assert audit.counts['mismatched'] == counters['venue_bbo_mismatch']
    return dict(schema='btc5m-bounded-bbo-diagnostic-v1', run_id=manifest.get('run_id'),
        scope='already_consumed_development_prefix_only_not_full_segment',
        configured_max_physical_batches=max_batches, prefix=prefix,
        canonical_prefix_events_sha256=event_digest.hexdigest(),
        input_manifest_sha256=hashlib.sha256((standard/'manifest.json').read_bytes()).hexdigest(),
        diagnostic_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        replay_code_sha256=hashlib.sha256(Path(lag.__file__).read_bytes()).hexdigest(),
        checks=audit.result(), counters=dict(counters),
        first_depth_mismatch_raw_context=first_depth_witness(standard/'clob_events.jsonl.gz', audit),
        labels_loaded=False, ignored_unscored_records=len(ignored_rows),
        economic_pnl_usd=None, rank7_pnl_usd=None, formal_oos_result=None,
        note='Awaiting-snapshot describes the existing latch, not its original cause. Prefix composition cannot explain full-segment counts or establish a strategy edge.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--max-batches', type=int, default=50000)
    parser.add_argument('--examples-per-state', type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.archive, max_batches=args.max_batches,
        examples_per_state=args.examples_per_state), indent=2, allow_nan=False))
