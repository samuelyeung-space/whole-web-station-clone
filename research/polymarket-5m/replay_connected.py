"""Connect verified historical caches to the existing offline replay entrypoints.

No download, live order, model fitting or new execution assumptions. Native
and legacy inputs stay separate. A fixed-100 full-or-zero capacity simulation
is the default here; it is not a promise that live FAK orders fill atomically.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def resolve_cache(data_root, dataset_key):
    if len(dataset_key) != 64 or any(c not in '0123456789abcdef' for c in dataset_key):
        raise ValueError('dataset must be its exact lowercase SHA256 cache key')
    root = Path(data_root).expanduser().resolve()
    found = [p.parent for p in root.rglob('manifest.json') if p.parent.name == dataset_key]
    if len(found) != 1:
        raise ValueError(f'expected one mounted dataset {dataset_key}, found {len(found)}; restore its existing data payload first')
    cache = found[0].resolve()
    if not cache.is_relative_to(root):
        raise ValueError('cache resolves outside the data root')
    return cache


def requires_legacy_assumptions(header):
    if header.get('execution_mode') == 'up_only_limited' or header.get('strict_execution_admitted') is False:
        return True
    # Merged caches retain each original header in nested provenance.
    sources = header.get('provenance', {}).get('source_headers', [])
    return any(requires_legacy_assumptions(source) for source in sources)


def command_for(args, cache, manifest):
    legacy = requires_legacy_assumptions(manifest['dataset_header'])
    if legacy and not args.accept_legacy_assumptions:
        raise ValueError('legacy input needs --accept-legacy-assumptions; it is not strict execution evidence')
    catalog = any(spec.rsplit(':', 1)[-1] == '*' for spec in args.strategy)
    if args.shared and catalog:
        raise ValueError('shared cash cannot be divided into catalog shards; select explicit strategies')
    cmd = [sys.executable, str(HERE / ('replay_catalog_batch.py' if catalog else 'replay_lab.py'))]
    if not catalog:
        cmd.append('run')
    cmd += ['--cache', str(cache), '--out', str(Path(args.out).resolve()),
            '--cash', '1000', '--shares', '100', '--latencies-ms', '500,600,1000',
            '--mode', 'shared' if args.shared else 'isolated',
            '--require-full-days', str(args.require_full_days)]
    if args.fill_policy == 'full-or-zero':
        cmd.append('--full-or-zero')
    if legacy:
        cmd += ['--side-specific-books', '--allow-assumed-constraints']
    for spec in args.strategy:
        cmd += ['--strategy', spec]
    for folder in args.plugin_dir:
        cmd += ['--plugin-dir', str(Path(folder).resolve())]
    if catalog:
        cmd += ['--batch-size', '120', '--workers', '1',
                '--batch-dir', str(Path(args.out).resolve() / 'catalogs')]
    return cmd


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root', required=True)
    p.add_argument('--dataset', required=True, help='exact dataset key, not a market ID')
    p.add_argument('--strategy', action='append', required=True, help='module:ID or module:*')
    p.add_argument('--out', default='connected-replay-runs')
    p.add_argument('--plugin-dir', action='append', default=[])
    p.add_argument('--shared', action='store_true')
    p.add_argument('--accept-legacy-assumptions', action='store_true')
    p.add_argument('--fill-policy', choices=['full-or-zero', 'partial-fak'], default='full-or-zero')
    p.add_argument('--require-full-days', type=int, default=0)
    p.add_argument('--dry-run', action='store_true', help='verify input and show command without an economic run')
    args = p.parse_args(argv)
    try:
        if args.require_full_days < 0:
            raise ValueError('required full days must be nonnegative')
        import replay_dataset
        cache = resolve_cache(args.data_root, args.dataset)
        manifest = replay_dataset.verify_cache(cache)
        cmd = command_for(args, cache, manifest)
        print(json.dumps({'dataset_key': args.dataset, 'initial_cash_usd': 1000,
                          'requested_shares': 100, 'fill_policy': args.fill_policy,
                          'formal_oos_result': None, 'command': cmd}), flush=True)
        if args.dry_run:
            return 0
        return subprocess.run(cmd, cwd=HERE, check=False).returncode
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc), 'pnl': None}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
