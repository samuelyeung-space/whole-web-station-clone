"""Run bounded, consumed-development ablations; never an OOS/rank7 runner."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

import mine500_replay_v2 as execution
import trend_pullback_context as context
import trend_pullback_ablation as strategy


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def augmented_events(input_path, spot_path, audit):
    """Merge only spot receipts already available at each original decision."""
    ctx = context.TrendContext()
    source = iter(execution.read_jsonl(spot_path))
    upcoming = next(source, None)
    books, markets = {}, {}
    counts = Counter()
    for row in execution.read_jsonl(input_path):
        kind, now, market = row.get('type'), row.get('recv_ms'), row.get('market_id')
        if kind == 'dataset':
            row = dict(row)
            row['split'] = 'development'
            row['parent_input_manifest_sha256'] = row['input_manifest_sha256']
            row['trend_context_parameters'] = ctx.audit()['parameters']
            row['new_feature_classification'] = 'receipt_time_observed_trade_bars_not_exchange_OHLC'
            row['formal_oos_eligible'] = False
        elif kind == 'market':
            markets[market] = row
        elif kind == 'book':
            books[market] = row
        elif kind == 'decision':
            while upcoming is not None and upcoming['recv_ms'] <= now:
                ctx.ingest(upcoming)
                upcoming = next(source, None)
            features = ctx.snapshot(now)
            features.update(ctx_market_start_ms=markets[market]['start_ms'], ctx_decision_ms=now)
            book, error = execution._book(books.get(market), now, execution.ReplayConfig())
            # Zero is an explicit invalid-quote sentinel, never a fillable price.
            # Immediate controls still reach the engine's normal rejection path.
            for side in ('Up', 'Down'):
                features['ctx_ask_' + side.lower()] = book[side][0][0] if not error and book[side] else 0.0
            row = dict(row)
            row['features'] = dict(row['features'], **features)
            row['feature_available_ms'] = dict(row['feature_available_ms'], **{
                k: now if v is not None else None for k, v in features.items()})
            counts['decisions'] += 1
            counts['context_ready' if all(features[k] is not None for k in context.FEATURE_NAMES)
                   else 'context_unavailable'] += 1
        yield row
    audit.update(context=ctx.audit(), decisions=dict(counts))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', required=True)
    ap.add_argument('--spot', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--protocol-only', action='store_true')
    args = ap.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    source_dir = Path(__file__).parent
    protocol = {
        'classification': 'consumed_Oct9_development_only_not_full_week_not_OOS',
        'input_sha256': sha(args.input), 'spot_sha256': sha(args.spot),
        'source_code_sha256': {p.name: sha(p) for p in [
            Path(__file__), Path(execution.__file__), Path(context.__file__),
            Path(strategy.__file__), source_dir / 'mine500_constraints.py']},
        'candidates': strategy.catalog(),
        'cash_per_arm_scenario': 1000, 'target_shares': 100,
        'scenarios': ['total_latency_500ms', 'total_latency_600ms', 'total_latency_1000ms',
                      '500ms_half_displayed_depth', '500ms_extra_1cent_per_filled_share'],
        'full_week_pnl': None, 'formal_oos_result': None,
        'volume_and_aggressor_confirmation': 'pending_native_direction_and_lifecycle_evidence',
        'cohort_caveat': 'REFERENCE is unrestricted; other arms share context readiness. '
                         'COMMON control comparisons are separate from reference-readiness differences.',
    }
    path = output / 'trend_ablation_protocol.json'
    encoded = json.dumps(protocol, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text() != encoded:
        raise ValueError('Registered protocol differs; use a new experiment directory')
    path.write_text(encoded)
    if args.protocol_only:
        print(json.dumps({'protocol': str(path), 'sha256': sha(path), 'executed': False}))
        return
    configs = [execution.ReplayConfig((500, 600, 1000), 100, 1000, require_constraints=True),
               execution.ReplayConfig((500,), 100, 1000, require_constraints=True, depth_fraction=.5),
               execution.ReplayConfig((500,), 100, 1000, require_constraints=True,
                                      extra_cost_per_filled_share_usd=.01)]
    metrics, audits = [], []
    for index, config in enumerate(configs):
        strategy.reset()
        feature_audit = {}
        result = execution.replay_events(augmented_events(args.input, args.spot, feature_audit),
                                         strategy.catalog(), strategy.signal, config)
        result['trend_feature_audit'] = feature_audit
        result['signal_state_audit'] = strategy.audit()
        result['protocol_sha256'] = sha(path)
        result['formal_oos_result'] = None
        result['full_week_pnl'] = None
        with gzip.open(output / f'trend_ablation_scenario{index}.json.gz', 'wt') as f:
            json.dump(result, f, ensure_ascii=False, separators=(',', ':'))
        for row in result['summary']:
            metrics.append(dict(row, scenario_group=index, full_week_pnl=None, formal_oos_result=None))
        audits.append({'scenario_group': index, 'feature_audit': feature_audit,
                       'signal_audit': result['signal_state_audit']})
    (output / 'trend_ablation_metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    (output / 'trend_ablation_audits.json').write_text(json.dumps(audits, indent=2) + '\n')
    print(json.dumps({'summary_rows': len(metrics), 'full_week_pnl': None, 'formal_oos': None}))


if __name__ == '__main__':
    main()
