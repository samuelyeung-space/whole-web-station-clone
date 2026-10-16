"""Describe completed frozen MAR rules without changing or fitting a strategy."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from historical_batch_summary import digest, read_csv
from replay_capacity_comparison import load_report


def assess(report_dir, feature_coverage, prediction_coverage):
    report_dir = Path(report_dir)
    summary, registration, configs, rules, provenance = load_report(report_dir, prediction_coverage)
    results = summary['results']
    if len(rules) != 144 or len(results) != 432:
        raise ValueError('Expected unchanged 144-rule by three-latency MAR baseline')
    if Counter((r['candidate_id'], r['latency_ms']) for r in results) != Counter(
            (cid, latency) for cid in rules for latency in (500, 600, 1000)):
        raise ValueError('Grid differs from frozen registration')
    if any(c['shares'] != 5 or c['depth_fraction'] != 1 or c['extra_cost_per_filled_share_usd'] != 0 for c in configs.values()):
        raise ValueError('Expected only five-share baseline')
    feature_days = json.loads(Path(feature_coverage).read_text())
    predicted = set(json.loads(Path(prediction_coverage).read_text())['model_prediction_dates'])
    if any(set(days) != predicted for days in feature_days['evaluable_dates_by_window'].values()):
        raise ValueError('Each MAR window requires its own feature-valid uncertainty dates')
    daily = list(read_csv(report_dir / 'daily.csv'))
    grouped = defaultdict(list)
    for row in results:
        grouped[row['candidate_id']].append(row)
    positive = lambda r: r['execution_complete_on_recorded_scope'] and r['recorded_scope_net_pnl_usd'] is not None and r['recorded_scope_net_pnl_usd'] > 0

    def describe(cid):
        return {'rule': rules[cid], 'alternative_latencies': grouped[cid],
                'all_date_daily_ledger': [r for r in daily if r['candidate_id'] == cid]}

    best = []
    for mechanism in sorted({c['mechanism'] for c in rules.values()}):
        rows = [r for r in results if r['latency_ms'] == 500 and rules[r['candidate_id']]['mechanism'] == mechanism]
        winner = max(rows, key=lambda r: r['recorded_scope_net_pnl_usd'] if r['recorded_scope_net_pnl_usd'] is not None else float('-inf'))
        best.append({'mechanism': mechanism, 'registered_rules': len(rows),
                     'positive_500ms_rules': sum(positive(r) for r in rows),
                     'descriptive_best_already_consumed_development': describe(winner['candidate_id'])})
    overlap = json.loads((report_dir / 'execution_overlap.json').read_text())
    value = {'schema': 'completed-mar-assessment-v1', 'new_backtests_by_assessment': 0,
             'completed_execution_scenarios': len(results), 'registered_rules': len(rules),
             'new_candidate_rules': 0, 'formal_oos_result': None, 'qualified_strategies': 0,
             'source': provenance, 'analysis_code_sha256': digest(__file__),
             'dependency_sha256': {'replay_capacity_comparison.py': digest(Path(__file__).with_name('replay_capacity_comparison.py')),
                                   'historical_batch_summary.py': digest(Path(__file__).with_name('historical_batch_summary.py'))},
             'feature_coverage_source': {'path': str(Path(feature_coverage).resolve()), 'sha256': digest(feature_coverage)},
             'prediction_dates_for_model_uncertainty': sorted(predicted),
             'excluded_warmup_dates': sorted(set(feature_days['by_market_start_utc_date']) - predicted),
             'missing_feature_denominators': feature_days,
             'execution_status_counts': dict(Counter(r['engine_status'] for r in results)),
             'reconciliation_differences': summary['reconciliation_differences'],
             'positive_recorded_scope_candidates_by_latency': {str(l): sum(positive(r) for r in results if r['latency_ms'] == l) for l in (500, 600, 1000)},
             'candidates_positive_in_all_three_alternative_latencies': sum(all(positive(r) for r in rs) for rs in grouped.values()),
             'fixed_before_results_representatives': [describe(cid) for cid in ('MAR-001', 'MAR-049', 'MAR-097')],
             'mechanism_descriptive_best': best,
             'observed_overlap': [{k: v for k, v in latency.items() if k in ('latency_ms', 'registered_candidates', 'distinct_nonempty_observed_attempt_sequences', 'no_attempt_candidates', 'exact_nonempty_sequence_groups')}
                                  for scenario in overlap['scenarios'] for latency in scenario['latencies']],
             'model_uncertainty_policy': 'Only dates with valid required features (Oct8/9); excluded no-score warmup is still in all coverage denominators. Original generic three-date cash-path uncertainty retained separately, never used as model return CI. Two partial development dates do not establish significance.',
             'prior_selection': 'Original book_5/model and MAR thresholds already selected with Oct9 development; earlier-date extension remains retrospective development, not OOS.',
             'scope': 'Each rule and latency has an independent $1000 account; taker FAK fixed signal-time limit held to official Gamma outcome. Displayed depth gives only a capacity upper bound. No maker queue, hedge or scale-in is implemented by these frozen rules.',
             'no_full_seven_days': True, 'full_calendar_pnl_nonnull_rows': sum(r['net_pnl_usd'] is not None for r in results),
             'rules': '144 parameter variants across three mechanisms; mean/reference slopes are observed BTC trailing means, not official settlement TWAP. Required fields remain unchanged even when slope/smooth gates are off.'}
    (report_dir / 'assessment.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', required=True)
    parser.add_argument('--feature-coverage', required=True)
    parser.add_argument('--prediction-coverage', required=True)
    args = parser.parse_args()
    value = assess(args.report_dir, args.feature_coverage, args.prediction_coverage)
    print(json.dumps({'out': str(Path(args.report_dir) / 'assessment.json'), 'scenarios': value['completed_execution_scenarios'], 'new_backtests': 0}))
