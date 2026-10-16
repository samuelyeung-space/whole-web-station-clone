"""One offline entrypoint: import recordings, cache once, replay strategy plugins.

No network client, orders, production writes or automatic model training.
All output is development simulation. Frozen historical protocols stay intact.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib
import itertools
import json
from pathlib import Path
import sys
import tempfile
import time


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def dump(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def numbers(text, kind=float):
    result = tuple(kind(x.strip()) for x in text.split(','))
    if not result or len(set(result)) != len(result):
        raise argparse.ArgumentTypeError('nonempty unique comma-separated values required')
    return result


def import_inputs(paths, root, input_type='auto'):
    from replay_dataset import ingest
    from replay_raw import prepare_raw
    normalized = []
    raw = []
    for value in paths:
        path = Path(value)
        kind = input_type
        if kind == 'auto':
            kind = 'normalized' if path.name.endswith(('.jsonl', '.jsonl.gz')) else 'raw'
        if kind == 'normalized':
            normalized.append(path)
        else:
            result = prepare_raw(path, Path(root)/'raw')
            raw.append({k: result[k] for k in ('cache_key', 'cache_hit', 'elapsed_seconds')})
            normalized.append(result['normalized_path'])
    result = ingest(normalized, Path(root)/'datasets')
    result['raw_imports'] = raw
    return result


def run_cached(args, cache_path=None):
    import replay_dataset as ds
    import replay_runner as runner
    import replay_reporting as reports
    started = time.perf_counter()
    for directory in args.plugin_dir:
        sys.path.insert(0, str(Path(directory).resolve()))
    cache = Path(cache_path or args.cache).resolve()
    manifest = ds.verify_cache(cache)
    loaded = runner.load_strategies(args.strategy)
    configs = []
    stresses = {'base': (1., 0.), 'half_depth': (.5, 0.), 'plus_1cent': (1., .01)}
    stress_names = args.stresses.split(',')
    if len(set(stress_names)) != len(stress_names) or any(n not in stresses for n in stress_names):
        raise ValueError('stresses must be unique: base,half_depth,plus_1cent')
    for size, stress, delay in itertools.product(args.shares, stress_names, args.release_delays_ms):
        depth, extra = stresses[stress]
        config = runner.ex.ReplayConfig(shares=size, initial_cash_usd=args.cash,
            latencies_ms=args.latencies_ms, max_quote_age_ms=args.max_quote_age_ms,
            depth_fraction=depth, extra_cost_per_filled_share_usd=extra,
            cash_release_delay_ms=delay, full_or_zero=args.full_or_zero,
            require_constraints=True)
        configs.append({'name': f'shares{size:g}_{stress}_release{delay}', 'config': asdict(config)})
    modules = [sys.modules[__name__], ds, runner, runner.ex, reports,
               importlib.import_module('mine500_constraints')]
    code = {Path(m.__file__).name: sha(m.__file__) for m in modules}
    strategy_identity = {k: v for k, v in loaded.registration.items() if k != 'plugin_source_paths'}
    identity = {'schema': 'replay-lab-run-v1', 'cache_key': manifest['dataset_key'],
        'events_sha256': manifest['tape_sha256'], 'strategies': strategy_identity,
        'mode': args.mode, 'priority': args.priority, 'scenarios': configs,
        'code_sha256': code, 'require_full_days': args.require_full_days,
        'formal_oos_result': None}
    key = fingerprint(identity)
    root = Path(args.out).resolve(); root.mkdir(parents=True, exist_ok=True)
    destination = root/key
    if destination.exists():
        complete = json.loads((destination/'completion.json').read_text())
        if complete['run_key'] != key:
            raise ValueError('existing run identity mismatch')
        for name, digest in complete['output_sha256'].items():
            if sha(destination/name) != digest:
                raise ValueError('cached run output corrupted: '+name)
        return {**complete, 'run_path': str(destination), 'cache_hit': True,
                'lookup_elapsed_seconds': time.perf_counter()-started, 'new_backtests': 0}
    stage = Path(tempfile.mkdtemp(prefix=key+'.pending.', dir=root))
    registration = {**identity, 'run_key': key,
        'plugin_source_paths': loaded.registration['plugin_source_paths'],
        'registered_at_utc': datetime.now(timezone.utc).isoformat(),
        'cash_scope': 'shared portfolio per latency' if args.mode == 'shared' else 'separate per candidate and latency',
        'input_data_role': 'consumed_development', 'model_fitting': False,
        'capacity_interpretation': 'displayed-depth simulation upper bound; no observed private fills',
        'markets': manifest.get('coverage', {}).get('markets', manifest.get('markets', []))}
    dump(stage/'registration.json', registration)
    try:
        coverage = manifest.get('coverage', manifest)
        days = coverage.get('daily', [])
        def complete(day):
            return (day.get('observed_seconds', 0) >= 86400
                    and day.get('complete_span_slots', 0) == 288
                    and day.get('data_present_slots', 0) == 288)
        complete_days = [d for d in days if complete(d)]
        longest_run = running = 0
        for day in days:
            running = running + 1 if complete(day) else 0
            longest_run = max(longest_run, running)
        # A missing calendar interval is an explicit blocked request, not a
        # profit observation of zero. Partial datasets can still be run normally.
        if args.require_full_days < 0:
            raise ValueError('require-full-days must be nonnegative')
        if args.require_full_days and longest_run < args.require_full_days:
            raise ValueError(f'request requires {args.require_full_days} consecutive complete recording days; only {len(complete_days)} complete days, longest run {longest_run}; see dataset coverage')
        events = list(ds.iter_events(cache))
        metrics = []
        for scenario in configs:
            tick = time.perf_counter()
            result = runner.execute(events, loaded, scenario['config'], mode=args.mode, priority=args.priority)
            result['scenario'] = scenario['name']
            result['wall_seconds_execution'] = time.perf_counter()-tick
            scenario_dir = stage/scenario['name']
            reports.write_reports(scenario_dir, result, coverage, registration)
            metrics.extend({'scenario': scenario['name'], **row} for row in result['summary'])
            print(json.dumps({'progress': scenario['name'], 'summary_rows': len(result['summary']),
                'seconds': round(result['wall_seconds_execution'], 3)}), flush=True)
        dump(stage/'metrics.json', metrics)
        dump(stage/'coverage.json', coverage)
        complete = {'run_key': key, 'status': 'completed_development_simulation',
            'candidate_count': len(loaded.candidates), 'execution_scenario_rows': len(metrics),
            'scenario_count': len(configs), 'mode': args.mode,
            'wall_seconds_total': time.perf_counter()-started,
            'formal_oos_result': None, 'qualified_strategies': 0,
            'output_sha256': {str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        dump(stage/'completion.json', complete)
        stage.rename(destination)
        return {**complete, 'run_path': str(destination), 'cache_hit': False,
                'new_backtests': len(metrics)}
    except Exception as exc:
        dump(stage/'failed.json', {'status': 'failed_or_blocked', 'error': str(exc),
            'net_pnl_usd': None, 'formal_oos_result': None, 'run_key': key})
        raise ValueError(f'run blocked; retained registration and diagnostics at {stage}: {exc}') from exc


def add_run_options(parser, with_cache=True):
    if with_cache:
        parser.add_argument('--cache', required=True)
    parser.add_argument('--out', required=True, help='result root; content-addressed runs deduplicate repeat requests')
    parser.add_argument('--strategy', action='append', required=True, help='module:id or module:*; repeatable')
    parser.add_argument('--plugin-dir', action='append', default=[])
    parser.add_argument('--mode', choices=['isolated', 'shared'], default='isolated')
    parser.add_argument('--priority', nargs='*', default=None, help='shared tie priority candidate IDs; default catalog order')
    parser.add_argument('--shares', type=numbers, default=(5.,), help='comma-separated sizes, e.g. 5,10,20,100')
    parser.add_argument('--latencies-ms', type=lambda x: numbers(x, int), default=(500,600,1000))
    parser.add_argument('--release-delays-ms', type=lambda x: numbers(x,int), default=(0,))
    parser.add_argument('--cash', type=float, default=1000.)
    parser.add_argument('--stresses', default='base', help='base,half_depth,plus_1cent; scenarios never summed')
    parser.add_argument('--full-or-zero', action='store_true', help='full-depth sensitivity, not actual FAK')
    parser.add_argument('--max-quote-age-ms', type=int, default=250)
    parser.add_argument('--require-full-days', type=int, default=0, help='reject request if complete recording days are insufficient')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('import', help='admit one or more local normalized tapes or recording ZIP/TGZ/directories')
    p.add_argument('--input', nargs='+', required=True); p.add_argument('--cache-root', required=True)
    p.add_argument('--input-type', choices=['auto', 'raw', 'normalized'], default='auto')
    r = sub.add_parser('run', help='replay plugins against one reusable cache'); add_run_options(r)
    p = sub.add_parser('pipeline', help='import once then run; safe to repeat after new inputs arrive')
    p.add_argument('--input', nargs='+', required=True); p.add_argument('--cache-root', required=True)
    p.add_argument('--input-type', choices=['auto', 'raw', 'normalized'], default='auto'); add_run_options(p, False)
    p = sub.add_parser('inspect', help='show verified coverage and data limitations'); p.add_argument('--cache', required=True)
    p = sub.add_parser('catalog', help='inspect plugins without evaluating signals')
    p.add_argument('--strategy', action='append', required=True); p.add_argument('--plugin-dir', action='append', default=[])
    args = ap.parse_args(argv)
    try:
        if args.command == 'import':
            result = import_inputs(args.input, args.cache_root, args.input_type)
        elif args.command == 'pipeline':
            data = import_inputs(args.input, args.cache_root, args.input_type)
            result = run_cached(args, data['cache_path'])
        elif args.command == 'run':
            result = run_cached(args)
        elif args.command == 'inspect':
            from replay_dataset import verify_cache
            result = verify_cache(args.cache)
        else:
            for directory in args.plugin_dir:
                sys.path.insert(0, str(Path(directory).resolve()))
            from replay_runner import load_strategies
            result = load_strategies(args.strategy).registration
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc), 'pnl': None}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
