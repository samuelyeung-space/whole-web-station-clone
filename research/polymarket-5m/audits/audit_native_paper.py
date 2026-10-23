"""Reconcile recorded paper arithmetic with captured Gamma labels, never fills.

Input must be the hash-verified native capture selected by intake_native_capture.
This program cannot certify causal execution, share size, queue or rank7 admission.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


def winner(row):
    names = json.loads(row['outcomes']) if isinstance(row['outcomes'], str) else row['outcomes']
    prices = json.loads(row['outcomePrices']) if isinstance(row['outcomePrices'], str) else row['outcomePrices']
    if row.get('closed') is not True or len(names) != 2 or list(map(float, prices)) not in ([1., 0.], [0., 1.]):
        return None
    return names[list(map(float, prices)).index(1.)]


def audit(root):
    root = Path(root)
    manifest = json.loads((root/'capture-manifest.json').read_text())
    selected_hashes = []
    for entry in manifest['files']:
        path = root/entry['path']
        if path.exists():
            assert path.stat().st_size == entry['bytes']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
            selected_hashes.append(entry['path'])
    runtime = json.loads((root/'capture-runtime.json').read_text())
    snapshots = defaultdict(list)
    decisions = defaultdict(list)
    skips = Counter()
    for path in root.glob('raw/*/markets.jsonl.gz'):
        with gzip.open(path, 'rt') as f:
            for line in f:
                r = json.loads(line)
                if r.get('slug', '').startswith('btc-updown-5m-'):
                    snapshots[r['slug']].append(r)
    for path in root.glob('raw/*/decisions.jsonl.gz'):
        with gzip.open(path, 'rt') as f:
            for line in f:
                r = json.loads(line)
                if 'shares' not in r:
                    skips[r.get('skip', 'other')] += 1
                    continue
                decisions[(r['slug'], r['tau'], r['decided_ms'])].append(r)
    resolved = {slug: winner(max(rows, key=lambda r: r['_recorded_at_ms'])) for slug, rows in snapshots.items()}
    groups = defaultdict(lambda: dict(rows=0, shares=0., fee=0., recorded_pnl_usd=0., official_reconciled_pnl_usd=0.))
    details = []
    with (root/'ledger.csv').open() as f:
        for row in csv.DictReader(f):
            key = (row['slug'], int(row['tau']), int(row['decided_ms']))
            match = [d for d in decisions[key] if d.get('side') == row['side'] and 'shares' in d]
            assert len(match) == 1, ('ambiguous paper decision', key)
            d = match[0]
            shares, ask, fee, cost, pnl = (float(row[k]) for k in ('shares', 'ask', 'fee', 'cost', 'pnl'))
            for k, v in [('shares', shares), ('ask', ask), ('fee', fee), ('cost', cost)]:
                assert abs(float(d[k])-v) < 1e-8
            pre = [r for r in snapshots[row['slug']] if r['_recorded_at_ms'] <= key[2]]
            assert pre, ('missing predecision market metadata', key)
            m = max(pre, key=lambda r: r['_recorded_at_ms'])
            schedule = m['feeSchedule']
            assert schedule['exponent'] == 1
            assert abs(fee-shares*float(schedule['rate'])*ask*(1-ask)) < 1e-8
            assert abs(cost-(shares*ask+fee)) < 1e-8
            w = resolved[row['slug']]
            assert w is not None, ('missing official captured Gamma outcome', key)
            won = w == row['side']
            assert row['won'] == str(won)
            official_pnl = shares*won-cost
            assert abs(official_pnl-pnl) < 1e-8
            g = groups[str(key[1])]
            for k, v in [('rows', 1), ('shares', shares), ('fee', fee), ('recorded_pnl_usd', pnl), ('official_reconciled_pnl_usd', official_pnl)]:
                g[k] += v
            details.append(dict(slug=row['slug'], tau_s=key[1], decision_ms=key[2], official_label=w,
                                source_reference_age_ms=key[2]-d['btc'][0], fee_rate=schedule['rate'],
                                predecision_snapshot_ms=m['_recorded_at_ms'], paper_pnl_usd=pnl))
    utc = lambda ms: datetime.fromtimestamp(ms/1000, timezone.utc).isoformat()
    return dict(schema='native-paper-label-arithmetic-audit-v1', run_id=manifest['run_id'],
                classification='recorded paper arithmetic only; no causal execution or financial engine replay',
                capture_utc=[utc(runtime['started_ms']), utc(runtime['ended_ms'])],
                capture_hours=(runtime['ended_ms']-runtime['started_ms'])/3600000,
                unique_btc5m_markets=len(snapshots), official_resolved_markets=sum(v is not None for v in resolved.values()),
                complete_utc_days=0, selected_manifest_hashes_verified=selected_hashes,
                native_paper_rows=len(details), groups=dict(groups), details=details, skips=dict(skips),
                capture_region=runtime['collector_region'], full_feed_health_verified=runtime['feed_health_verified'],
                fee_rounding_scope='recorded unrounded paper formula; not actual bill reconciliation',
                official_labels_not_ohlc=True, new_causal_backtests=0, actual_fills_verified=0,
                rank7_pnl_usd=None, formal_oos_result=None)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('output'); a = p.parse_args()
    r = audit(a.input)
    Path(a.output).write_text(json.dumps(r, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: r[k] for k in ['capture_utc', 'capture_hours', 'unique_btc5m_markets', 'official_resolved_markets', 'native_paper_rows', 'groups']}))
