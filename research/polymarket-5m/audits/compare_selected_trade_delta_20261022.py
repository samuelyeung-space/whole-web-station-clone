"""Compare two already-published selected-trade files, without replaying them."""
import base64
import collections
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path


def read(path):
    raw = base64.b64decode(Path(path).read_text())
    return raw, list(csv.DictReader(io.StringIO(gzip.decompress(raw).decode())))


def identity(row):
    return tuple(row[k] for k in ('variant', 'lag', 'run', 'market_id', 't', 'side'))


def same(a, b):
    for key in a:
        if a[key] == b[key]:
            continue
        try:
            if math.isclose(float(a[key]), float(b[key]), rel_tol=0, abs_tol=1e-12):
                continue
        except ValueError:
            pass
        return False
    return True


if __name__ == '__main__':
    import sys
    new_raw, new_rows = read(sys.argv[1])
    old_raw, old_rows = read(sys.argv[2])
    new = {identity(r): r for r in new_rows}
    old = {identity(r): r for r in old_rows}
    assert len(new) == len(new_rows) and len(old) == len(old_rows), 'duplicate keys'
    added = [new[k] for k in new.keys() - old.keys()]
    removed = [old[k] for k in old.keys() - new.keys()]
    changed = [k for k in new.keys() & old.keys() if not same(new[k], old[k])]
    print(json.dumps({
        'schema': 'selected-trade-cumulative-delta-v1',
        'new_rows': len(new_rows), 'old_rows': len(old_rows),
        'new_gzip_sha256': hashlib.sha256(new_raw).hexdigest(),
        'old_gzip_sha256': hashlib.sha256(old_raw).hexdigest(),
        'added_rows': len(added), 'removed_rows': len(removed),
        'materially_changed_common_rows': len(changed),
        'added_by_run': dict(collections.Counter(r['run'] for r in added)),
        'removed_by_run': dict(collections.Counter(r['run'] for r in removed)),
        'changed_by_run': dict(collections.Counter(k[2] for k in changed)),
        'changed_fields': dict(collections.Counter(
            field for key in changed for field in new[key]
            if not same({field: new[key][field]}, {field: old[key][field]}))),
        'new_run397104_rows_by_variant_latency': dict(collections.Counter(
            r['variant'] + '/' + r['lag'] for r in added if r['run'] == '39710494768')),
        'independent_new_oos': False,
        'notes': ['Selected trades omit full attempted-order/cash states and cannot validate current V3 or Rank7.',
                  'Tolerance 1e-12 applies only to numeric serialization changes.'],
    }, indent=2, ensure_ascii=False))
