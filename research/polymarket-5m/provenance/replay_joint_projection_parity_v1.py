"""Check JPR decision/execution inputs are unchanged in MAR's superset tapes."""
import argparse
from collections import Counter
import gzip
import hashlib
import itertools
import json
from pathlib import Path

from frozen_jpr_strategy import catalog, PROVENANCE


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def project(row, fields):
    if row['type'] == 'dataset':
        return {'type': 'dataset'}  # Different adapters must have different identities.
    if row['type'] == 'decision':
        return {'type': 'decision', 'market_id': row['market_id'], 'recv_ms': row['recv_ms'],
                'features': {k: row.get('features', {}).get(k) for k in fields},
                'feature_available_ms': {k: row.get('feature_available_ms', {}).get(k) for k in fields}}
    return row


def verify(jpr_paths, mar_manifest, out):
    mar = json.loads(Path(mar_manifest).read_text())
    fields = sorted({'tau_s'} | {k for c in catalog() for k in c['required_features']})
    if len(jpr_paths) != len(mar['inputs']):
        raise ValueError('Input tape counts differ')
    tapes = []
    for left, right in zip(jpr_paths, mar['inputs']):
        lp, rp = Path(left), Path(right['output_path'])
        if sha(rp) != right['output_sha256']:
            raise ValueError('MAR manifest hash mismatch')
        counts = Counter()
        projection = hashlib.sha256()
        with gzip.open(lp, 'rt') as lf, gzip.open(rp, 'rt') as rf:
            for index, (a, b) in enumerate(itertools.zip_longest(lf, rf)):
                if a is None or b is None:
                    raise ValueError('Event count differs')
                a, b = json.loads(a), json.loads(b)
                pa, pb = project(a, fields), project(b, fields)
                if pa != pb:
                    raise ValueError(f'JPR execution/feature projection differs at {lp}:{index}')
                projection.update(json.dumps(pa, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n')
                counts[a['type']] += 1
        tapes.append({'jpr_path': str(lp), 'jpr_sha256': sha(lp), 'mar_path': str(rp),
                      'mar_sha256': right['output_sha256'], 'event_counts': dict(counts),
                      'projection_sha256': projection.hexdigest()})
    value = {'schema': 'jpr-mar-superset-projection-parity-v1', 'status': 'passed',
             'code_sha256': sha(__file__), 'plugin_source_sha256': sha(Path(__file__).with_name('frozen_jpr_strategy.py')),
             'original_rule_provenance': PROVENANCE, 'projected_jpr_fields': fields, 'tapes': tapes,
             'policy': 'All JPR catalog required fields and tau_s, availability clocks, market/constraint/book/outcome events, event order and decisions are identical. Adapter dataset identity alone intentionally differs. No strategy fitting, rule changes or economic replay performed.',
             'new_backtests': 0, 'formal_oos_result': None}
    Path(out).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jpr-input', nargs='+', required=True)
    parser.add_argument('--mar-manifest', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print(json.dumps({'status': verify(args.jpr_input, args.mar_manifest, args.out)['status'], 'new_backtests': 0}))
