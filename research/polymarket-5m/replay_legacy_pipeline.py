"""One-command offline legacy ZIP -> verified cache -> chronological strategy replay.

This is the explicit historical Up-L1 sensitivity lane, NOT strict execution
or a new OOS protocol. No Arrow dependency, network calls, trading or training.
Repeated imports verify cached bytes; missing/overlapping inputs fail closed.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

import replay_dataset as ds
import replay_lab as lab
from historical_replay_batch import inspect_boundary


def expand_inputs(names):
    paths = []
    for name in names:
        path = Path(name).expanduser().resolve()
        found = sorted(path.rglob('*.zip')) if path.is_dir() else [path]
        if not found:
            raise ValueError('no ZIP recordings found: ' + str(path))
        for item in found:
            if not item.is_file():
                raise ValueError('missing recording: ' + str(item))
            if item.suffix.lower() != '.zip':
                raise ValueError('legacy input must be a ZIP: ' + str(item))
            if item not in paths:
                paths.append(item)
    return paths


def prepare_legacy(path, root, cutoff_ms, max_book_age_ms=250, max_source_age_ms=1000):
    import legacy_up_l1_adapter as adapter
    import lake_parquet_adapter
    import legacy_recording_cache
    identity = {
        'schema': 'replay-legacy-pipeline-v1', 'input_sha256': lab.sha(path),
        'adapter_sha256': lab.sha(adapter.__file__),
        'feature_sha256': lab.sha(lake_parquet_adapter.__file__),
        'clock_sha256': lab.sha(legacy_recording_cache.__file__),
        'pipeline_sha256': lab.sha(__file__),
        'settlement_cutoff_ms': cutoff_ms,
        'max_book_age_ms': max_book_age_ms, 'max_source_age_ms': max_source_age_ms,
    }
    destination = Path(root) / lab.fingerprint(identity)
    tape, receipt = destination / 'events.jsonl.gz', destination / 'intake.json'
    reused = destination.exists()
    if reused:
        if not receipt.is_file():
            raise ValueError('incomplete legacy cache; retained for inspection: ' + str(destination))
        record = json.loads(receipt.read_text())
        if record['identity'] != identity or record['output_sha256'] != lab.sha(tape):
            raise ValueError('legacy cache integrity mismatch: ' + str(destination))
    else:
        destination.mkdir(parents=True, exist_ok=False)
        try:
            result = adapter.normalize(path, tape, settlement_cutoff_ms=cutoff_ms,
                                       max_book_age_ms=max_book_age_ms,
                                       max_source_age_ms=max_source_age_ms)
            # Exhausts gzip, validates event order, fields, footer and counts.
            scan = ds._scan(tape)
            if scan['counts'] != result['counts']:
                raise ValueError('normalized event counts do not reconcile')
            record = {'identity': identity, 'output_sha256': lab.sha(tape),
                      'counts': result['counts'], 'registered_markets': result['registered_markets']}
            lab.dump(receipt, record)
        except Exception as exc:
            lab.dump(destination / 'failed.json', {'error': str(exc), 'pnl': None})
            raise
    scan = ds._scan(tape)
    if scan['counts'] != record['counts']:
        raise ValueError('cached event counts do not reconcile')
    return {'path': str(tape), 'cache_hit': reused, 'identity': identity,
            'registered_markets': record['registered_markets']}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', nargs='+', required=True, help='local ZIPs or directories; all selected files are retained')
    p.add_argument('--cache-root', default='.replay-cache')
    p.add_argument('--development-end-before', required=True, help='exclusive ISO timestamp with UTC/offset')
    p.add_argument('--settlement-cutoff-ms', required=True, type=float, help='one predeclared cutoff after every market end')
    p.add_argument('--accept-legacy-assumptions', action='store_true',
                   help='explicitly accept retrospective metadata and Up-only displayed-depth sensitivity')
    p.add_argument('--max-source-age-ms', type=int, default=1000)
    lab.add_run_options(p, with_cache=False)
    return p


def execute(args):
    if not args.accept_legacy_assumptions:
        raise ValueError('legacy history needs --accept-legacy-assumptions; it cannot be called strict execution')
    cutoff = datetime.fromisoformat(args.development_end_before.replace('Z', '+00:00'))
    if cutoff.tzinfo is None:
        raise ValueError('development boundary requires an explicit UTC/offset')
    if not math.isfinite(args.settlement_cutoff_ms):
        raise ValueError('finite settlement cutoff required')
    if args.max_source_age_ms < 0 or args.max_quote_age_ms < 0:
        raise ValueError('age limits must be nonnegative')
    paths = expand_inputs(args.input)
    root = Path(args.cache_root).resolve()
    started = time.perf_counter()
    imports = []
    for path in paths:
        item = prepare_legacy(path, root / 'legacy', args.settlement_cutoff_ms,
                              args.max_quote_age_ms, args.max_source_age_ms)
        inspect_boundary(item['path'], cutoff.timestamp() * 1000)
        imports.append(item)
    # A single merged tape is replayed once, so cash is never reset per ZIP.
    data = lab.import_inputs([x['path'] for x in imports], root, 'normalized')
    args.side_specific_books = True
    args.allow_assumed_constraints = True
    result = lab.run_cached(args, data['cache_path'])
    receipt = {**result, 'legacy_imports': imports, 'dataset_cache_path': data['cache_path'],
               'strict_execution_admitted': False, 'formal_oos_result': None,
               'wall_seconds': time.perf_counter() - started,
               'development_end_before': cutoff.isoformat()}
    lab.dump(Path(result['run_path']) / 'legacy_pipeline_receipt.json', receipt)
    return receipt


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc), 'pnl': None}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
