"""Portable launcher for the existing BTC5m offline replay lab. Never trades.

  python3 replay_cli.py doctor
  python3 replay_cli.py demo --work demo-output
  python3 replay_cli.py run --input recording.zip --strategy 'my_rules:ONE'
  python3 replay_cli.py run --cache CACHE_PATH --strategy 'my_rules:ONE'

The demo is hand-computable synthetic test data, not historical performance.
Existing replay_lab and historical_replay_batch own all simulation semantics.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
CORE = ('replay_lab', 'replay_dataset', 'replay_raw', 'replay_engine',
        'replay_runner', 'replay_reporting', 'replay_plugin_identity',
        'mine500_constraints')
DEMO_PLUGIN = """# Synthetic acceptance strategy only; not an investment strategy.
def catalog():
    return [{'id': 'DEMO-TAKER', 'required_features': ['x']}]

def signal(candidate, features):
    return 'Up' if features['x'] > 0 else None
"""


def output(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def doctor():
    checks = {}
    for name in CORE:
        try:
            module = importlib.import_module(name)
            checks[name] = {'ok': True, 'path': str(Path(module.__file__).resolve())}
        except Exception as exc:
            checks[name] = {'ok': False, 'error': str(exc)}
    ready = sys.version_info >= (3, 11) and all(v['ok'] for v in checks.values())
    return {'status': 'ready' if ready else 'not_ready', 'python': sys.version,
            'minimum_python': '3.11', 'checks': checks,
            'optional_numpy_available': importlib.util.find_spec('numpy') is not None,
            'network_or_orders': False, 'historical_coverage_verified': False}


def demo_rows():
    # Six five-minute synthetic slots; no calendar-day coverage claim.
    base = 1767225600000  # 2026-01-01 UTC; deliberately synthetic.
    cases = ('full', 'partial', 'no_fill', 'stale', 'no_signal', 'missing_feature')
    rows = [{'type': 'dataset', 'recv_ms': base, 'dataset_id': 'portable-demo-v1',
             'split': 'development', 'provenance': {'source': 'SYNTHETIC_ACCEPTANCE_ONLY'},
             'coverage_start_ms': base, 'coverage_end_ms': base + 1800000,
             'limitations': ['Synthetic test fixture, not recorded market data.']}]
    for index, case in enumerate(cases):
        start = base + index * 300000
        mid = 'demo-' + case
        rows += [
            {'type': 'market', 'recv_ms': start, 'market_id': mid,
             'start_ms': start, 'end_ms': start + 300000, 'label_source': 'gamma'},
            {'type': 'constraint', 'recv_ms': start, 'market_id': mid,
             'min_order_size': 5, 'up_tick_size': .01, 'down_tick_size': .01,
             'constraints_source': 'gamma'}]
        for dt in (100, 600, 700, 1100):
            if case == 'stale' and dt != 100:
                continue
            price = .41 if case == 'no_fill' and dt != 100 else .4
            depth = 2 if case == 'partial' and dt != 100 else 20
            rows.append({'type': 'book', 'recv_ms': start + dt, 'market_id': mid,
                         'up_asks': [[price, depth]], 'down_asks': [[.6, 20]],
                         'healthy': True, 'fee_rate': .07})
            if dt == 100:
                features = {'tau_s': 299.9}
                if case != 'missing_feature':
                    features['x'] = 0 if case == 'no_signal' else 1
                rows.append({'type': 'decision', 'recv_ms': start + dt, 'market_id': mid,
                             'features': features,
                             'feature_available_ms': {k: start + dt for k in features}})
        rows.append({'type': 'outcome', 'recv_ms': start + 302000, 'market_id': mid,
                     'resolved_ms': start + 300000, 'winner': 'Up', 'source': 'gamma'})
    return sorted(rows, key=lambda row: row['recv_ms'])


def write_same_or_new(path, text):
    if path.exists():
        if not path.is_file() or path.read_text(encoding='utf-8') != text:
            raise ValueError('refusing to overwrite changed demo input: ' + str(path))
    else:
        path.write_text(text, encoding='utf-8')


def demo(argv):
    parser = argparse.ArgumentParser(description='Run synthetic end-to-end acceptance, not historical PnL.')
    parser.add_argument('--work', default='demo-output')
    args = parser.parse_args(argv)
    root = Path(args.work).resolve()
    root.mkdir(parents=True, exist_ok=True)
    tape = root / 'synthetic.jsonl'
    write_same_or_new(root / 'portable_demo_strategy.py', DEMO_PLUGIN)
    write_same_or_new(tape, ''.join(json.dumps(r, allow_nan=False) + '\n' for r in demo_rows()))
    command = [sys.executable, str(HERE / 'replay_lab.py'), 'pipeline',
               '--input', str(tape), '--input-type', 'normalized',
               '--plugin-dir', str(root), '--strategy', 'portable_demo_strategy:DEMO-TAKER',
               '--cache-root', str(root / 'cache'), '--out', str(root / 'runs')]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip())
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    run = Path(result['run_path'])
    metrics = json.loads((run / 'metrics.json').read_text())
    coverage = json.loads((run / 'coverage.json').read_text())
    for row in metrics:
        if (row['attempts'] != 4 or row['full_fills'] != 1 or row['partial_fills'] != 1
                or row['filled_shares'] != 7
                or row['status_counts'].get('no_fill') != 1
                or row['status_counts'].get('rejected_stale_book_at_match') != 1
                or abs(row['fees_usd'] - .1176) > 1e-8
                or abs(row['ending_cash_usd'] - 1004.0824) > 1e-8):
            raise ValueError('synthetic hand-calculation mismatch: ' + json.dumps(row))
    if len(metrics) != 3 or coverage['registered_markets'] != 6:
        raise ValueError('synthetic market/scenario counts do not reconcile')
    receipt = {'status': 'synthetic_acceptance_passed', 'synthetic_only': True,
               'historical_performance': None, 'registered_markets': 6,
               'scenarios': 3, 'attempts_per_scenario': 4,
               'full_fills_per_scenario': 1, 'partial_fills_per_scenario': 1,
               'no_fills_per_scenario': 1, 'stale_rejections_per_scenario': 1,
               'no_signal_markets': 1, 'missing_feature_markets': 1,
               'cache_hit': result['cache_hit'], 'new_backtests': result['new_backtests'],
               'run_path': str(run), 'reports': [str(p) for p in run.glob('*/report.md')]}
    (root / 'acceptance.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    output(receipt)
    return 0


def route(command, args):
    args = list(args)
    if command == 'batch':
        import historical_replay_batch
        return historical_replay_batch.main(args)
    import replay_lab
    def has(name):
        return any(v == name or v.startswith(name + '=') for v in args)
    if command == 'run' and has('--input'):
        if has('--cache'):
            raise ValueError('choose --input or --cache, not both')
        command = 'pipeline'
    if command in ('run', 'pipeline') and not has('--out'):
        args += ['--out', 'replay-runs']
    if command in ('import', 'pipeline') and not has('--cache-root'):
        args += ['--cache-root', '.replay-cache']
    return replay_lab.main([command, *args])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        print('Commands: doctor, demo, verify, run, pipeline, import, inspect, catalog, batch')
        return 0
    if sys.version_info < (3, 11):
        output({'status': 'error', 'error': 'Python 3.11+ required', 'pnl': None})
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == 'doctor':
            if args:
                raise ValueError('doctor takes no arguments')
            result = doctor(); output(result)
            return 0 if result['status'] == 'ready' else 2
        if command == 'demo':
            return demo(args)
        if command == 'verify':
            if args:
                raise ValueError('verify takes no arguments')
            return subprocess.call([sys.executable, '-m', 'unittest', 'discover',
                                    '-s', str(HERE), '-p', 'test_replay_*.py', '-v'], cwd=HERE)
        if command not in {'run', 'pipeline', 'import', 'inspect', 'catalog', 'batch'}:
            raise ValueError('unknown command: ' + command)
        return route(command, args)
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc), 'pnl': None}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
