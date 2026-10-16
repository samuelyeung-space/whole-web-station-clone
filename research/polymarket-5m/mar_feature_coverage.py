"""Audit the full MAR decision denominator without executing or selecting rules."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

from frozen_mar_strategy import candidates


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def audit(manifest_path, output):
    manifest = json.loads(Path(manifest_path).read_text())
    rules = candidates()
    required = {w: set(next(r['required_features'] for r in rules if r['window'] == w)) | {'tau_s'}
                for w in (15, 60, 120)}
    by_day = defaultdict(lambda: {'decisions': 0, 'saved_book_5_rows': 0,
                                  'windows': {str(w): {'valid_rows': 0, 'missing_rows': 0,
                                                       'missing_feature_counts': Counter(),
                                                       'future_or_unproven_feature_counts': Counter(),
                                                       'markets': set()} for w in required}})
    sources = []
    markets = {}
    for source in manifest['inputs']:
        path = Path(source['output_path'])
        actual = sha(path)
        if actual != source['output_sha256']:
            raise ValueError(f'Feature tape hash mismatch: {path}')
        sources.append({'path': str(path), 'sha256': actual})
        with gzip.open(path, 'rt') as f:
            for line in f:
                event = json.loads(line)
                if event['type'] == 'market':
                    markets[event['market_id']] = event
                elif event['type'] == 'decision':
                    market = markets[event['market_id']]
                    day = datetime.fromtimestamp(market['start_ms'] / 1000, timezone.utc).date().isoformat()
                    rec = by_day[day]
                    rec['decisions'] += 1
                    features = event['features']
                    availability = event.get('feature_available_ms', {})
                    rec['saved_book_5_rows'] += finite(features.get('book_5'))
                    for window, names in required.items():
                        stats = rec['windows'][str(window)]
                        missing = [name for name in names if not finite(features.get(name))]
                        unavailable = [name for name in names if finite(features.get(name)) and
                                       (not finite(availability.get(name)) or availability[name] > event['recv_ms'])]
                        stats['missing_feature_counts'].update(missing)
                        stats['future_or_unproven_feature_counts'].update(unavailable)
                        if missing or unavailable:
                            stats['missing_rows'] += 1
                        else:
                            stats['valid_rows'] += 1
                            stats['markets'].add(event['market_id'])
    for rec in by_day.values():
        for stats in rec['windows'].values():
            stats['valid_feature_markets'] = len(stats.pop('markets'))
    days = dict(sorted(by_day.items()))
    result = {'schema': 'mar-global-feature-denominator-v1', 'new_backtests': 0,
              'formal_oos_result': None, 'manifest_sha256': sha(manifest_path),
              'analysis_code_sha256': sha(__file__), 'source_tapes': sources,
              'registered_rules': len(rules), 'window_feature_sets': {str(w): sorted(v) for w, v in required.items()},
              'by_market_start_utc_date': days,
              'evaluable_dates_by_window': {str(w): [d for d, r in days.items() if r['windows'][str(w)]['valid_rows'] > 0] for w in required},
              'policy': 'Retain every decision, including warmup/missing rows. A date with zero valid feature rows is excluded from model return uncertainty, never inserted as a zero-return tradable day. Counts are before any first-signal/one-trade-per-market restriction and are not engine valid_feature_rows.',
              'original_requirements_preserved': 'All frozen catalog required fields remain required even with slope/smooth gates off; tau_s also read by original signal. No inference or imputation.'}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = audit(args.manifest, args.out)
    print(json.dumps({'output': args.out, 'dates': result['evaluable_dates_by_window'], 'new_backtests': 0}))
