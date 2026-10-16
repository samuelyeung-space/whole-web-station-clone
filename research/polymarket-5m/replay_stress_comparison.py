"""Compare frozen all-rule half-depth and extra-cost replay results with base."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from historical_batch_summary import digest, write_csv
from replay_capacity_comparison import load_report


def compare(baseline_report, stress_report, out, representatives, prediction_coverage):
    base = load_report(baseline_report, prediction_coverage)
    stress = load_report(stress_report, prediction_coverage)
    for field in ("cache_key", "events_sha256", "code_sha256"):
        if base[1][field] != stress[1][field]:
            raise ValueError("stress/base lineage differs: " + field)
    if base[3] != stress[3]:
        raise ValueError("stress/base rules differ")
    if len(base[2]) != 1 or len(stress[2]) != 2:
        raise ValueError("expected one base and two stress configs")
    base_config = next(iter(base[2].values()))
    if base_config["shares"] != 5 or base_config["depth_fraction"] != 1 or base_config["extra_cost_per_filled_share_usd"] != 0:
        raise ValueError("expected five-share full-depth zero-extra-cost baseline")
    for config in stress[2].values():
        differences = {k for k in base_config if config.get(k) != base_config[k]}
        if not ((differences == {"depth_fraction"} and config["depth_fraction"] == .5)
                or (differences == {"extra_cost_per_filled_share_usd"} and config["extra_cost_per_filled_share_usd"] == .01)):
            raise ValueError("unexpected stress configuration")
    if not set(representatives) <= base[3].keys():
        raise ValueError("unknown fixed representative")
    base_rows = {(r["candidate_id"],r["latency_ms"]):r for r in base[0]["results"]}
    expected = {(cid,latency,scenario) for cid in base[3] for latency in (500,600,1000) for scenario in stress[2]}
    observed = Counter((r["candidate_id"],r["latency_ms"],r["scenario"]) for r in stress[0]["results"])
    if observed != Counter(expected):
        raise ValueError("incomplete or duplicated all-rule stress grid")
    rows = []
    for source in stress[0]["results"]:
        row = dict(source)
        prior = base_rows[(row["candidate_id"],row["latency_ms"])]
        cfg = stress[2][row["scenario"]]
        row["scenario_config"] = cfg
        row["baseline_recorded_scope_net_pnl_usd"] = prior["recorded_scope_net_pnl_usd"]
        row["baseline_attempts"] = prior["attempts"]
        row["baseline_filled_orders"] = prior["filled_orders"]
        row["baseline_filled_shares"] = prior["filled_shares"]
        row["recorded_scope_pnl_difference_from_baseline_usd"] = row["recorded_scope_net_pnl_usd"] - prior["recorded_scope_net_pnl_usd"] if row["recorded_scope_net_pnl_usd"] is not None and prior["recorded_scope_net_pnl_usd"] is not None else None
        row["additional_cost_break_even_after_scenario_usd_per_filled_share_fixed_fills"] = row["recorded_scope_net_pnl_usd"] / row["filled_shares"] if row["recorded_scope_net_pnl_usd"] is not None and row["filled_shares"] else None
        rows.append(row)
    positive=lambda r: r["execution_complete_on_recorded_scope"] and r["recorded_scope_net_pnl_usd"] is not None and r["recorded_scope_net_pnl_usd"] > 0
    counts=[]
    for scenario in sorted(stress[2]):
        for latency in (500,600,1000):
            selected=[r for r in rows if r["scenario"]==scenario and r["latency_ms"]==latency]
            counts.append({"scenario":scenario,"latency_ms":latency,"registered_candidates":len(selected),
                "positive_recorded_scope_candidates":sum(positive(r) for r in selected),
                "engine_status_counts":dict(Counter(r["engine_status"] for r in selected)),
                "full_calendar_pnl_nonnull":sum(r["net_pnl_usd"] is not None for r in selected)})
    grouped=defaultdict(list)
    for row in rows+base[0]["results"]:
        grouped[row["candidate_id"]].append(row)
    value={"schema":"paired-execution-stress-comparison-v1","new_backtests_by_comparison":0,
        "baseline_already_completed_scenarios":len(base[0]["results"]),"stress_completed_scenarios":len(rows),
        "formal_oos_result":None,"qualified_strategies":0,"strategy_selection":False,
        "representatives_fixed_before_stress_results":representatives,
        "sources":[base[4],stress[4]],"code_sha256":digest(__file__),
        "comparison_helper_sha256":digest(Path(__file__).with_name('replay_capacity_comparison.py')),
        "prediction_coverage_sha256":digest(prediction_coverage),"positive_counts":counts,
        "candidates_positive_in_base_and_both_stresses_all_three_latencies":sum(all(positive(r) for r in rs) for rs in grouped.values()),
        "representative_rules":{cid:base[3][cid] for cid in representatives},
        "representative_rows":[r for r in rows if r["candidate_id"] in representatives],
        "reconciliation_differences":[],
        "cost_stress_definition":"plus_1cent is a replayed cash reservation/charge of $0.01 per actual filled share; it does not change the signal-time limit or book match price. half_depth applies 0.5 to displayed matching depth.",
        "uncertainty_policy":"Main model CI uses prediction dates only. Original all-date cash-path CI is preserved separately in each row; input coverage includes warmup.",
        "interpretation":"Paired alternative scenarios, never additive. These are partial-recording development results, depth-based fill upper bounds, not 7-day/OOS or independent strategies."}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'stress_comparison.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_csv(out/'stress_comparison_all_rules.csv',rows)
    write_csv(out/'stress_comparison_representatives.csv',value['representative_rows'])
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline-report','stress-report','out','representatives','prediction-coverage'):
        p.add_argument('--'+name,required=True)
    a=p.parse_args();r=compare(a.baseline_report,a.stress_report,a.out,a.representatives.split(','),a.prediction_coverage)
    print(json.dumps({'out':a.out,'new_backtests':0,'stress_completed_scenarios':r['stress_completed_scenarios']}))
