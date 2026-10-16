"""Compare completed size scenarios; never add PnL or start a replay."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from historical_batch_summary import day_bootstrap, digest, read_csv, write_csv


def load_report(directory, prediction_coverage):
    directory = Path(directory)
    path = directory / "summary.json"
    summary = json.loads(path.read_text())
    if summary.get("reconciliation_differences"):
        raise ValueError("unreconciled summary")
    if len(summary["provenance"]) != 1:
        raise ValueError("one completed source run per report required")
    provenance = summary["provenance"][0]
    run = Path(provenance["source_run"])
    completion = json.loads((run / "completion.json").read_text())
    if completion["status"] != "completed_development_simulation":
        raise ValueError("incomplete source replay")
    if digest(run / "completion.json") != provenance["completion_sha256"]:
        raise ValueError("summary/completion lineage differs")
    registration = json.loads((run / "registration.json").read_text())
    if digest(run / "registration.json") != completion["output_sha256"]["registration.json"]:
        raise ValueError("registration hash mismatch")
    configs = {s["name"]: s["config"] for s in registration["scenarios"]}
    rules = {r["id"]: r for r in registration["strategies"]["candidate_rules"]}
    prediction_days = set(json.loads(Path(prediction_coverage).read_text())["model_prediction_dates"])
    daily = list(read_csv(directory / "daily.csv"))
    for r in summary["results"]:
        if r["run_key"] != completion["run_key"]:
            raise ValueError("cross-run row")
        if r["mode"] != "isolated":
            raise ValueError("this comparison requires isolated per-rule accounts")
        eligible_days = [d for d in daily if d["candidate_id"] == r["candidate_id"]
            and d["latency_ms"] == r["latency_ms"] and d["scenario"] == r["scenario"]
            and d["market_start_utc_date"] in prediction_days]
        seed = int(hashlib.sha256(f"{r['candidate_id']}:{r['latency_ms']}:{r['scenario']}:prediction_days".encode()).hexdigest()[:16],16)
        r["program_cash_path_uncertainty_including_warmup"] = r["day_cluster_uncertainty"]
        r["day_cluster_uncertainty"] = day_bootstrap(eligible_days, samples=2000, seed=seed)
        r["day_cluster_uncertainty_basis"] = "Only dates with saved model predictions; excludes no-prediction warmup. Few partial dates and previous selection do not establish significance."
        r["prediction_dates_for_uncertainty"] = sorted(prediction_days)
    return summary, registration, configs, rules, {"path":str(path.resolve()), "sha256":digest(path),
        "completion_sha256":provenance["completion_sha256"], "run_key":completion["run_key"]}


def compare(baseline_report, capacity_report, out, representatives, prediction_coverage):
    baseline = load_report(baseline_report, prediction_coverage)
    capacity = load_report(capacity_report, prediction_coverage)
    if baseline[1]["cache_key"] != capacity[1]["cache_key"] or baseline[1]["events_sha256"] != capacity[1]["events_sha256"]:
        raise ValueError("different input cache; size comparison is not paired")
    if baseline[3] != capacity[3] or baseline[1]["code_sha256"] != capacity[1]["code_sha256"]:
        raise ValueError("different rules/core; cannot attribute comparison to size alone")
    if not set(representatives) <= baseline[3].keys():
        raise ValueError("unknown fixed representative")
    rows, unique = [], set()
    config_except_size = None
    for report, registration, configs, rules, provenance in (baseline, capacity):
        for source in report["results"]:
            cfg = configs[source["scenario"]]
            other = {k:v for k,v in cfg.items() if k != "shares"}
            if config_except_size is None:
                config_except_size = other
            elif config_except_size != other:
                raise ValueError("non-size scenario parameter differs")
            size = cfg["shares"]
            key = source["candidate_id"], size, source["latency_ms"]
            if key in unique:
                raise ValueError("duplicate candidate/size/latency")
            unique.add(key)
            row = dict(source)
            row["requested_shares"] = size
            row["rule_mechanism"] = rules[source["candidate_id"]]["mechanism"]
            row["historical_baseline_not_new_replay"] = report is baseline[0]
            shares, pnl = source["filled_shares"], source["recorded_scope_net_pnl_usd"]
            denominator = source["submitted_orders"] * size
            row["filled_share_fraction_of_submitted_requested_shares"] = shares / denominator if denominator else None
            row["extra_cost_break_even_usd_per_filled_share_fixed_fills"] = pnl / shares if shares and pnl is not None else None
            row["extra_cost_interpretation"] = "Algebraic incremental cost threshold conditional on identical fills and signals. Not a new execution/cash replay; slippage can change fills, signals and cash eligibility."
            rows.append(row)
    expected = {(cid, size, latency) for cid in baseline[3] for size in (5,10,20) for latency in (500,600,1000)}
    if unique != expected:
        raise ValueError("comparison must cover exact 5/10/20 by 500/600/1000 full candidate grid")
    positive = lambda r: r["execution_complete_on_recorded_scope"] and r["recorded_scope_net_pnl_usd"] is not None and r["recorded_scope_net_pnl_usd"] > 0
    counts = []
    for size in (5,10,20):
        for latency in (500,600,1000):
            subset = [r for r in rows if r["requested_shares"] == size and r["latency_ms"] == latency]
            counts.append({"requested_shares":size, "latency_ms":latency,
                "registered_candidates":len(subset), "positive_recorded_scope_candidates":sum(positive(r) for r in subset),
                "execution_status_counts":dict(Counter(r["engine_status"] for r in subset)),
                "full_calendar_pnl_nonnull":sum(r["net_pnl_usd"] is not None for r in subset)})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["candidate_id"]].append(row)
    representative_rows = [r for r in rows if r["candidate_id"] in representatives]
    tolerance = []
    for cid in representatives:
        for size in (5,10,20):
            chosen = [r for r in representative_rows if r["candidate_id"] == cid and r["requested_shares"] == size]
            values = [r["extra_cost_break_even_usd_per_filled_share_fixed_fills"] for r in chosen]
            tolerance.append({"candidate_id":cid, "requested_shares":size,
                "minimum_across_three_latencies_usd_per_filled_share": min(values) if all(v is not None for v in values) else None,
                "positive_all_three_latencies":all(positive(r) for r in chosen)})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    value = {"schema":"paired-capacity-comparison-v1", "new_backtests_by_comparison":0,
        "baseline_already_completed_scenarios":len(baseline[0]["results"]),
        "capacity_completed_scenarios":len(capacity[0]["results"]),
        "formal_oos_result":None, "qualified_strategies":0, "strategy_selection":False,
        "representatives_fixed_before_capacity_results":representatives,
        "sources":[baseline[4],capacity[4]], "code_sha256":digest(__file__),
        "uncertainty_correction": {"prediction_coverage_path":str(Path(prediction_coverage).resolve()),
            "prediction_coverage_sha256":digest(prediction_coverage),
            "main_model_ci":"Only prediction dates; original generic cash-path CI preserved in each row under program_cash_path_uncertainty_including_warmup.",
            "source_summaries_or_ledgers_modified":False, "all_date_coverage_denominators_preserved":True},
        "dataset_key":baseline[1]["cache_key"], "events_sha256":baseline[1]["events_sha256"],
        "scenario_config_except_size":config_except_size, "reconciliation_differences":[],
        "positive_counts":counts,
        "candidates_positive_in_all_nine_alternative_scenarios":sum(all(positive(r) for r in rs) for rs in grouped.values()),
        "representative_rules":{cid:baseline[3][cid] for cid in representatives},
        "representative_rows":representative_rows, "representative_cost_tolerance":tolerance,
        "policy":"No PnL sum across size, latency or candidate. Each alternative uses an independent $1000 account. Retain all rules in historical trial denominator.",
        "limitations":["Development partial-recording scope only; no full 7-day or OOS result.",
            "Displayed depth estimates capacity upper bounds; no observed fill validation.",
            "Only size changes. Additional cost threshold is algebraic conditional on unchanged executions; it does not implement changed limit prices, nonlinear market impact or cash-trigger feedback.",
            "Fixed representatives describe their mechanisms but do not prove independent returns."]}
    (out / "capacity_comparison.json").write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    write_csv(out / "capacity_comparison_all_rules.csv", sorted(rows,key=lambda r:(r["candidate_id"],r["requested_shares"],r["latency_ms"])))
    write_csv(out / "capacity_comparison_representatives.csv", sorted(representative_rows,key=lambda r:(r["candidate_id"],r["requested_shares"],r["latency_ms"])))
    return value


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report",required=True)
    parser.add_argument("--capacity-report",required=True)
    parser.add_argument("--out",required=True)
    parser.add_argument("--representatives",required=True,help="comma-separated IDs fixed before capacity results")
    parser.add_argument("--prediction-coverage",required=True)
    args=parser.parse_args()
    result=compare(args.baseline_report,args.capacity_report,args.out,args.representatives.split(','),args.prediction_coverage)
    print(json.dumps({"out":args.out,"new_backtests":0,"capacity_completed_scenarios":result["capacity_completed_scenarios"]}))
