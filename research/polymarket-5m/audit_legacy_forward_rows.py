"""Audit legacy per-unit forward CSVs without claiming executable dollar PnL.

The size column is displayed quote depth, never an order quantity. This tool
checks immutable row identities and the recorded fee equation; it neither
validates official labels nor repairs the older simulation's timing semantics.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

KEY = ('variant', 'lag', 'run', 'market_id', 't', 'side')
NUMERIC = ('lag', 't', 'price', 'fee', 'size', 'won', 'pnl', 'p0', 'fair')
FIELDS = set(KEY) | set(NUMERIC)


def read_rows(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if not FIELDS.issubset(reader.fieldnames or []):
            raise ValueError('missing legacy forward columns')
        rows = list(reader)
    return rows


def checked(rows):
    indexed, problems, stats = {}, [], defaultdict(list)
    for number, raw in enumerate(rows, 1):
        try:
            row = {k: raw[k] for k in FIELDS}
            for key in NUMERIC:
                row[key] = float(row[key])
            if any(not math.isfinite(row[k]) for k in NUMERIC):
                raise ValueError('nonfinite')
            if (not 0 < row['price'] < 1 or row['won'] not in (0., 1.) or
                    row['size'] < 0 or row['lag'] < 0 or row['t'] <= 0 or
                    not row['market_id'].startswith('btc-updown-5m-') or
                    row['side'] not in ('Up', 'Down')):
                raise ValueError('invalid domain')
        except (ValueError, TypeError, KeyError):
            problems.append({'row': number, 'reason': 'invalid_schema_or_value'})
            continue
        identity = tuple(row[k] for k in KEY)
        if identity in indexed:
            problems.append({'row': number, 'reason': 'duplicate_identity', 'key': identity})
            continue
        indexed[identity] = row
        if abs(row['fee'] - .07 * row['price'] * (1. - row['price'])) > 1e-12:
            problems.append({'row': number, 'reason': 'fee_equation_mismatch', 'key': identity})
        if abs(row['pnl'] - (row['won'] - row['price'] - row['fee'])) > 1e-12:
            problems.append({'row': number, 'reason': 'per_unit_pnl_mismatch', 'key': identity})
        stats[(row['variant'], row['lag'])].append(row['pnl'])
    summary = [dict(variant=v, lag_seconds=lag, rows=len(vals),
                    mean_recorded_net_cents_per_unit=100*math.fsum(vals)/len(vals))
               for (v, lag), vals in sorted(stats.items())]
    return indexed, problems, summary


def audit(old_rows, new_rows):
    old, old_errors, old_summary = checked(old_rows)
    new, new_errors, new_summary = checked(new_rows)
    changed = [{'key': key, 'changed_fields': {f: {'old': old[key][f], 'new': new[key][f]}
                for f in sorted(FIELDS) if old[key][f] != new[key][f]}}
               for key in sorted(old.keys() & new.keys()) if old[key] != new[key]]
    added, removed = sorted(new.keys()-old.keys()), sorted(old.keys()-new.keys())
    # Do not confuse binary floating-point drift with changed economics. The
    # immutable-evidence gate stays strict even for those small differences.
    tiny, material = [], []
    for item in changed:
        fields = item['changed_fields']
        target = tiny if all(f in NUMERIC and abs(v['new']-v['old']) <= 1e-12
                             for f,v in fields.items()) else material
        target.append(item)
    churn = defaultdict(lambda: dict(added=0, removed=0))
    for key in added:
        churn[key[2]]['added'] += 1
    for key in removed:
        churn[key[2]]['removed'] += 1
    _, delta_errors, added_summary = checked([new[key] for key in added])
    passed = bool(old and new) and not (old_errors or new_errors or changed or removed or delta_errors)
    return dict(schema='btc5m-legacy-forward-row-audit-v1', passed=passed,
        old_rows=len(old_rows), new_rows=len(new_rows),
        old_unique_rows=len(old), new_unique_rows=len(new),
        old_errors=old_errors, new_errors=new_errors,
        changed_existing_rows=changed, removed_keys=removed, added_keys=added,
        tiny_numeric_changed_rows=len(tiny), material_changed_rows=len(material),
        maximum_changed_numeric_abs_difference=max((abs(v['new']-v['old'])
            for item in changed for f,v in item['changed_fields'].items() if f in NUMERIC), default=0.),
        per_run_identity_churn=dict(sorted(churn.items())),
        old_summary=old_summary, new_summary=new_summary, added_summary=added_summary,
        dollar_pnl=None, rank7_result=None, formal_oos_result=None,
        official_labels_independently_verified=False,
        fee_effective_time_independently_verified=False,
        new_financial_replays=0,
        scope='Published legacy per-unit arithmetic only; size is quote depth, not executed shares. '
              'No private fill, cash reuse, fixed100, common cohort or OOS qualification.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', required=True, type=Path)
    parser.add_argument('--new', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    result = audit(read_rows(args.old), read_rows(args.new))
    result['input_sha256'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for name, path in [('old', args.old), ('new', args.new)]}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('passed', 'old_rows', 'new_rows')}))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
