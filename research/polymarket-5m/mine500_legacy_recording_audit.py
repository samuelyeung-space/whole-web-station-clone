"""Inventory legacy forward ZIP/tar/gzip tapes without inventing missing clocks.

This reads every BTC member and emits coverage/schema evidence, never a PnL.
Post-resolution Gamma metadata is evidence of final labels and parameter values,
not proof those labels/parameters were locally known at an earlier decision.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile


def audit(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    result = dict(input_sha256=digest.hexdigest(), members=[], markets=[],
                  evidence_class='historical_input_inventory_only', pnl=None)
    with zipfile.ZipFile(path) as z:
        if z.namelist() != ['raw.tgz']:
            raise ValueError('unexpected outer archive layout')
        with tarfile.open(fileobj=z.open('raw.tgz'), mode='r|gz') as tar:
            for member in tar:
                if not member.isfile() or not member.name.startswith('bundle-btc/'):
                    continue
                if not member.name.endswith(('.jsonl.gz', '.csv.gz')):
                    raise ValueError('unrecognised BTC member format')
                stats = dict(path=member.name, rows=0, recv_min_ms=None, recv_max_ms=None)
                fields, payload_fields, events = Counter(), Counter(), Counter()
                prior = None
                regressions = 0
                with gzip.GzipFile(fileobj=tar.extractfile(member)) as gz:
                    with io.TextIOWrapper(gz, encoding='utf-8') as text:
                        rows = (json.loads(line) for line in text) if '.jsonl.' in member.name else csv.DictReader(text)
                        for row in rows:
                            stats['rows'] += 1
                            fields.update(row.keys())
                            payload_fields.update(row.get('payload', {}).keys())
                            events.update([row.get('event', 'unspecified')])
                            now = row.get('recv_ms')
                            if now is None and row.get('receive_ts') is not None:
                                now = float(row['receive_ts']) * 1000
                            if now is not None:
                                now = float(now)
                                stats['recv_min_ms'] = min(now, stats['recv_min_ms'] or now)
                                stats['recv_max_ms'] = max(now, stats['recv_max_ms'] or now)
                                regressions += prior is not None and now < prior
                                prior = now
                            if '/markets/BTC-5m/' in member.name:
                                raw = row.get('raw', {})
                                result['markets'].append(dict(slug=row.get('slug'),
                                    start_sec=row.get('start_sec'), end_sec=row.get('end_sec'),
                                    resolved=row.get('resolved'), outcome_prices=row.get('outcome_prices'),
                                    resolution_source=raw.get('resolutionSource'),
                                    fee_schedule=raw.get('feeSchedule'),
                                    min_order_size=raw.get('orderMinSize'),
                                    tick_size=raw.get('orderPriceMinTickSize'),
                                    gamma_closed_time=raw.get('closedTime'),
                                    local_metadata_recv_ms=row.get('recv_ms'),
                                    local_settlement_known_ms=row.get('settlement_known_ms')))
                stats.update(field_counts=dict(fields), payload_field_counts=dict(payload_fields),
                             event_counts=dict(events), receipt_regressions=regressions)
                result['members'].append(stats)
    members = result['members']
    books = [x for x in members if '/book/BTC-5m/' in x['path']]
    spot = [x for x in members if x['path'].endswith('/binance_trades.jsonl.gz')]
    result['btc5m_book_rows'] = sum(x['rows'] for x in books)
    result['market_count'] = len({m['slug'] for m in result['markets']})
    # The separate probability-observation CSV does preserve both clocks. It
    # must not be confused with the token snapshots: proving token/side/depth
    # equivalence requires a separate join audit before using it to backfill.
    result['alternate_clocked_observation_rows'] = sum(x['rows'] for x in members
        if 'poly_probability_observations' in x['path']
        and 'source_ts' in x['field_counts'] and 'receive_ts' in x['field_counts'])
    result['snapshot_receipt_regressions_in_file_order'] = sum(x['receipt_regressions'] for x in books)
    result['next_permitted_work'] = [
        'Audit observation CSV token/side/depth mapping against receipt snapshots before any clock backfill.',
        'Register a separately named historical sensitivity protocol if missing continuity/availability requires assumptions; do not reuse unchanged EPM freeze.',
        'Use chronological prior-date training; do not score these dates with the October 9 trained model and call it forward OOS.'
    ]
    result['blockers_for_unchanged_epm_contract'] = []
    blockers = result['blockers_for_unchanged_epm_contract']
    if not books or any(x['field_counts'].get('event_ts_ms', 0) != x['rows'] for x in books):
        blockers.append('book source clock not recorded on every snapshot')
    if not spot or any(any(x['field_counts'].get(k, 0) != x['rows'] for k in ('connection_epoch', 'stream_sequence')) for x in spot):
        blockers.append('external endpoint connection epoch/sequence absent; continuity cannot be reconstructed')
    if any(m['local_metadata_recv_ms'] is None for m in result['markets']):
        blockers.append('Gamma metadata has no local receipt/effective clock; retrospective fee/tick values are not as-of evidence')
    if any(m['local_settlement_known_ms'] is None for m in result['markets']):
        blockers.append('Gamma close time exists but local settlement/cash availability clock is absent')
    result['unchanged_epm_replay_admitted'] = not blockers and bool(books and result['markets'])
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    result = audit(args.input)
    Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('members', 'markets')}))


if __name__ == '__main__':
    main()
