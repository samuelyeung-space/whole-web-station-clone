"""Count unique completed alternative scenarios without adding their PnL."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from historical_batch_summary import digest, read_csv


def inventory(capacity_comparison_csv, stress_comparison_csv, out):
    rows = list(read_csv(capacity_comparison_csv)) + list(read_csv(stress_comparison_csv))
    keys = Counter((r['candidate_id'], r['scenario'], r['latency_ms']) for r in rows)
    if any(n != 1 for n in keys.values()):
        raise ValueError('duplicated candidate/scenario/latency; baseline must appear once')
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['candidate_id']].append(row)
    expected_scenarios = {'shares5_base_release0','shares10_base_release0','shares20_base_release0',
                         'shares5_half_depth_release0','shares5_plus_1cent_release0'}
    expected_per_candidate = {(s,l) for s in expected_scenarios for l in (500,600,1000)}
    if any({(r['scenario'],r['latency_ms']) for r in rs} != expected_per_candidate for rs in grouped.values()):
        raise ValueError('incomplete 15-scenario grid')
    positive = lambda r: r['execution_complete_on_recorded_scope'] and r['recorded_scope_net_pnl_usd'] is not None and r['recorded_scope_net_pnl_usd'] > 0
    value = {'schema':'alternative-scenario-inventory-v1','new_backtests_by_inventory':0,
        'formal_oos_result':None,'qualified_strategies':0,'registered_rules':len(grouped),
        'unique_completed_candidate_scenarios':len(keys),'alternative_scenarios_per_rule':15,
        'engine_status_counts':dict(Counter(r['engine_status'] for r in rows)),
        'candidates_positive_in_all_15_alternatives':sum(all(positive(r) for r in rs) for rs in grouped.values()),
        'input_hashes':{str(Path(p).resolve()):digest(p) for p in (capacity_comparison_csv,stress_comparison_csv)},
        'code_sha256':digest(__file__),
        'source_run_keys':sorted({r['run_key'] for r in rows}),
        'model_uncertainty_day_cluster_counts':sorted({r['day_cluster_uncertainty']['day_clusters'] for r in rows}),
        'policy':'PnL never summed across alternative scenarios or candidates. Number of rows is not number of independent mechanisms; preserve all historical trial counts.',
        'coverage':'Partial development recordings, not seven complete days; formal OOS remains null.'}
    Path(out).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capacity-comparison-csv',required=True)
    p.add_argument('--stress-comparison-csv',required=True)
    p.add_argument('--out',required=True)
    a=p.parse_args()
    print(json.dumps(inventory(a.capacity_comparison_csv,a.stress_comparison_csv,a.out),ensure_ascii=False))
