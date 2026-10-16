"""Bound memory by replaying a fully preregistered isolated catalog in shards.

Uses replay_lab's immutable input and full output hash checks on every resume.
This is an execution partition, never a PnL screen or a shared-cash portfolio.
Only compact metrics are combined; attempts/cash ledgers remain in shard runs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import gc
import importlib
import json
from pathlib import Path
import sys
import time

import replay_lab as lab


def atomic_dump(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.pending')
    lab.dump(temporary, value)
    temporary.replace(path)


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    lab.add_run_options(ap)
    ap.add_argument('--batch-size', type=int, default=90,
                    help='maximum candidates per isolated run, in original catalog order')
    ap.add_argument('--batch-dir', required=True,
                    help='full-catalog registrations, compact metrics and resume progress')
    ap.add_argument('--workers', type=int, default=1, help='isolated shard worker processes; shared portfolios cannot be partitioned')
    return ap


def ordered_selectors(specs, loaded, runner):
    """Resolve ownership without assuming signal.__module__ owns a catalog.

    A plugin is allowed to import signal from another module. Re-expand each
    original selection, and require the same rules/hashes as the full catalog.
    This also rejects nondeterministic catalog providers before execution.
    """
    selectors, rules = [], []
    for spec in specs:
        part = runner.load_strategies([spec])
        name = spec.rsplit(':', 1)[0]
        if (part.plugin_code_sha256[name] != loaded.plugin_code_sha256[name] or
                part.registration['plugin_dependency_identity'][name] !=
                loaded.registration['plugin_dependency_identity'][name]):
            raise ValueError('plugin changed while registering full catalog: ' + name)
        selectors.extend(name + ':' + candidate['id'] for candidate in part.candidates)
        rules.extend(part.candidates)
    if rules != loaded.candidates:
        raise ValueError('catalog changed while resolving candidate selectors')
    return selectors


def execute_shard(args, shard, code, plugin_hashes, dependency_identity, dataset_key, tape_sha256):
    import replay_runner as runner
    # Abort source drift before initiating the next economic run.
    for filename, digest in code.items():
        if lab.sha(Path(__file__).parent / filename) != digest:
            raise ValueError('replay source changed during catalog batch: ' + filename)
    subset = runner.load_strategies(shard['strategy_specs'])
    if (subset.registration['candidate_sha256'] != shard['candidate_sha256'] or
            any(plugin_hashes[name] != digest
                for name, digest in subset.plugin_code_sha256.items()) or
            any(dependency_identity[name] != value
                for name, value in subset.registration['plugin_dependency_identity'].items())):
        raise ValueError('strategy changed after full-catalog registration')
    shard_args = copy.deepcopy(args)
    shard_args.strategy = shard['strategy_specs']
    shard_args.priority = None
    result = lab.run_cached(shard_args)
    run_path = Path(result['run_path'])
    actual = json.loads((run_path / 'registration.json').read_text())
    if (actual['strategies']['candidate_sha256'] != shard['candidate_sha256'] or
            actual['strategies']['plugin_code_sha256'] != subset.plugin_code_sha256 or
            actual['strategies']['plugin_dependency_identity'] != subset.registration['plugin_dependency_identity'] or
            actual['cache_key'] != dataset_key or
            actual['events_sha256'] != tape_sha256):
        raise ValueError('shard result does not match full-catalog registration')
    rows = json.loads((run_path / 'metrics.json').read_text())
    if set(row['candidate_id'] for row in rows) != set(shard['candidate_ids']):
        raise ValueError('shard output does not cover all registered candidates')
    return shard, result, actual, rows


def completed_shards(args, shards, code, plugin_hashes, dependency_identity, dataset_key, tape_sha256):
    workers = getattr(args, 'workers', 1)
    if workers == 1:
        for shard in shards:
            yield execute_shard(args, shard, code, plugin_hashes, dependency_identity, dataset_key, tape_sha256)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute_shard, args, shard, code, plugin_hashes, dependency_identity, dataset_key, tape_sha256) for shard in shards]
        try:
            for future in as_completed(futures):
                yield future.result()
        finally:
            for future in futures:
                future.cancel()


def run_batch(args):
    started = time.perf_counter()
    if args.mode != 'isolated':
        raise ValueError('catalog sharding supports isolated mode only; shared cash/depth cannot be split')
    if getattr(args, 'workers', 1) < 1 or getattr(args, 'workers', 1) > 8:
        raise ValueError('workers must be between 1 and 8')
    if args.batch_size <= 0:
        raise ValueError('batch-size must be positive')
    # Isolated priority changes only iteration order. Keep the original catalog
    # order rather than silently partitioning a caller-supplied alternative.
    for directory in args.plugin_dir:
        sys.path.insert(0, str(Path(directory).resolve()))
    import replay_dataset as ds
    import replay_runner as runner
    manifest = ds.verify_cache(args.cache)
    loaded = runner.load_strategies(args.strategy)
    ids = [c['id'] for c in loaded.candidates]
    if args.priority is not None and list(args.priority) != ids:
        raise ValueError('isolated catalog batches require default catalog priority/order')
    selectors = ordered_selectors(args.strategy, loaded, runner)
    strategy = {k: v for k, v in loaded.registration.items() if k not in {'plugin_source_paths', 'plugin_dependency_paths'}}
    configuration = {key: copy.deepcopy(value) for key, value in vars(args).items()
                     if key not in {'cache', 'out', 'batch_dir', 'plugin_dir', 'strategy', 'command'}}
    # Paths do not alter economics. Exact source identity and data digest do.
    code = {Path(module.__file__).name: lab.sha(module.__file__) for module in
            [lab, ds, runner, runner.ex, importlib.import_module('replay_reporting'),
             importlib.import_module('mine500_constraints')]}
    code[Path(__file__).name] = lab.sha(__file__)
    shards = [{'index': index, 'candidate_ids': ids[start:start + args.batch_size],
               'strategy_specs': selectors[start:start + args.batch_size],
               'candidate_sha256': lab.fingerprint(loaded.candidates[start:start + args.batch_size])}
              for index, start in enumerate(range(0, len(ids), args.batch_size))]
    identity = {'schema': 'replay-catalog-batch-v1',
                'dataset_key': manifest['dataset_key'], 'events_sha256': manifest['tape_sha256'],
                'strategies': strategy, 'configuration': configuration,
                'code_sha256': code, 'shards': shards,
                'formal_oos_result': None}
    identity = json.loads(json.dumps(identity, allow_nan=False))
    key = lab.fingerprint(identity)
    destination = Path(args.batch_dir).resolve() / key
    destination.mkdir(parents=True, exist_ok=True)
    registration_path = destination / 'registration.json'
    if registration_path.exists():
        previous = json.loads(registration_path.read_text())
        if previous.get('batch_key') != key or previous.get('identity') != identity:
            raise ValueError('batch registration identity corrupted')
    else:
        registration = {'batch_key': key, 'identity': identity,
            'registered_at_utc': datetime.now(timezone.utc).isoformat(),
            'registered_candidate_count': len(ids), 'candidate_ids_in_catalog_order': ids,
            'input_cache_path': str(Path(args.cache).resolve()),
            'plugin_source_paths': loaded.registration['plugin_source_paths'],
            'plugin_dependency_paths': loaded.registration['plugin_dependency_paths'],
            'cash_scope': 'separate per candidate and latency and scenario',
            'multiple_testing_denominator_floor_this_catalog': len(ids),
            'multiple_testing_note': 'All catalog candidates registered before any shard runs. '
                'Prior/historical trials must also be retained; shards and resume are not extra independent mechanisms.',
            'simulation_only': True, 'input_data_role': 'consumed_development',
            'model_fitting': False, 'formal_oos_result': None,
            'aggregation_policy': 'No PnL selection or sums across candidates/latencies/scenarios.'}
        atomic_dump(registration_path, registration)
    # Check wrapper outputs too, but never use completion to bypass run_cached.
    completion_path = destination / 'completion.json'
    if completion_path.exists():
        previous = json.loads(completion_path.read_text())
        if previous.get('batch_key') != key:
            raise ValueError('batch completion identity corrupted')
        for name, digest in previous['output_sha256'].items():
            if lab.sha(destination / name) != digest:
                raise ValueError('batch output corrupted: ' + name)
    metrics, completed, new_backtests = [], [], 0
    try:
        for shard, result, actual, rows in completed_shards(
                args, shards, code, loaded.plugin_code_sha256, loaded.registration['plugin_dependency_identity'], manifest['dataset_key'], manifest['tape_sha256']):
            run_path = Path(result['run_path'])
            metrics.extend(rows)
            new_backtests += result['new_backtests']
            completed.append({'index': shard['index'], 'candidate_ids': shard['candidate_ids'],
                'run_key': result['run_key'], 'run_path': result['run_path'],
                'metrics_sha256': lab.sha(run_path / 'metrics.json'),
                'registration_sha256': lab.sha(run_path / 'registration.json'),
                'completion_sha256': lab.sha(run_path / 'completion.json'),
                'cache_hit': result['cache_hit'], 'new_backtests': result['new_backtests']})
            atomic_dump(destination / 'progress.json', {
                'batch_key': key, 'registered_candidate_count': len(ids),
                'completed_shards': completed, 'total_shards': len(shards),
                'status': 'in_progress', 'formal_oos_result': None,
                'new_execution_scenario_rows_this_invocation': new_backtests})
            print(json.dumps({'batch_progress': len(completed), 'total_shards': len(shards),
                  'registered_candidates': len(ids), 'shard_cache_hit': result['cache_hit']}), flush=True)
            del rows, result
            gc.collect()
        # Compact output order depends only on registered catalog and scenario
        # order, not on PnL. Detailed executions remain in their original runs.
        order = {candidate_id: index for index, candidate_id in enumerate(ids)}
        scenario_order = {scenario['name']: index for index, scenario in enumerate(actual['scenarios'])}
        latency_order = {value: index for index, value in enumerate(args.latencies_ms)}
        metrics.sort(key=lambda row: (order[row['candidate_id']], scenario_order[row['scenario']],
                                     latency_order[row['latency_ms']]))
        statuses = {cid: [] for cid in ids}
        for row in metrics:
            statuses[row['candidate_id']].append(row['status'])
        classifications = []
        for cid in ids:
            values = statuses[cid]
            if all(s.startswith('blocked_') for s in values):
                category = 'blocked_all_scenarios'
            elif all(s == 'simulated_complete' or s.startswith('evaluated_') for s in values):
                category = 'evaluated_all_scenarios'
            else:
                category = 'mixed_or_incomplete'
            classifications.append({'candidate_id': cid, 'classification': category,
                                    'scenario_status_counts': dict(Counter(values))})
        summary = {'batch_key': key, 'registered_candidate_count': len(ids),
            'multiple_testing_denominator_floor_this_catalog': len(ids),
            'historical_trials_additional_and_not_reset': True,
            'candidate_class_counts': dict(Counter(row['classification'] for row in classifications)),
            'scenario_status_counts': dict(Counter(row['status'] for row in metrics)),
            'execution_scenario_rows': len(metrics), 'candidates': classifications,
            'formal_oos_result': None, 'qualified_strategies': 0,
            'net_pnl_sum_across_candidates': None,
            'aggregation_policy': 'Independent candidates/latencies/scenarios cannot be summed as a portfolio.'}
        # Persist stable evidence independent of whether this call hit caches.
        completed.sort(key=lambda item: item['index'])
        stable_shards = [{k: v for k, v in item.items() if k not in {'cache_hit', 'new_backtests'}}
                         for item in completed]
        atomic_dump(destination / 'metrics.json', metrics)
        atomic_dump(destination / 'summary.json', summary)
        atomic_dump(destination / 'shards.json', stable_shards)
        complete = {'batch_key': key, 'status': 'completed_development_simulation',
            'registered_candidate_count': len(ids), 'shard_count': len(shards),
            'execution_scenario_rows': len(metrics), 'formal_oos_result': None,
            'qualified_strategies': 0,
            'output_sha256': {name: lab.sha(destination / name) for name in
                             ('registration.json', 'metrics.json', 'summary.json', 'shards.json')}}
        atomic_dump(completion_path, complete)
        atomic_dump(destination / 'progress.json', {**complete, 'completed_shards': completed,
                    'new_execution_scenario_rows_this_invocation': new_backtests})
        if (destination / 'failed.json').exists():
            (destination / 'failed.json').unlink()
        return {**complete, 'batch_path': str(destination), 'cache_hit': all(s['cache_hit'] for s in completed),
                'new_backtests': new_backtests, 'wall_seconds_this_invocation': time.perf_counter() - started}
    except Exception as exc:
        atomic_dump(destination / 'failed.json', {'batch_key': key, 'status': 'failed_or_blocked',
            'error': str(exc), 'registered_candidate_count': len(ids),
            'completed_shards': completed, 'net_pnl_usd': None, 'formal_oos_result': None})
        raise


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run_batch(args)
    except Exception as exc:
        print(json.dumps({'status': 'failed_or_blocked', 'error': str(exc), 'pnl': None}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
