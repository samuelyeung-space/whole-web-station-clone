"""Describe a completed frozen JPR replay; never execute or select a new rule."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from historical_batch_summary import day_bootstrap, digest, read_csv


def assess(run_dir, report_dir, prediction_coverage, admission_audit):
    run_dir, report_dir = Path(run_dir), Path(report_dir)
    read = lambda p: json.loads(Path(p).read_text())
    completion = read(run_dir / "completion.json")
    if completion["status"] != "completed_development_simulation":
        raise ValueError("completed development replay required")
    summary = read(report_dir / "summary.json")
    if summary["reconciliation_differences"]:
        raise ValueError("attempt/cash reconciliation differs")
    if summary["provenance"][0]["completion_sha256"] != digest(run_dir / "completion.json"):
        raise ValueError("summary is not bound to this completed run")
    reg = read(run_dir / "registration.json")
    rules = {r["id"]: r for r in reg["strategies"]["candidate_rules"]}
    results = summary["results"]
    if len(rules) != 96 or len(results) != 288:
        raise ValueError("expected frozen 96 by 3 JPR grid")
    if Counter((r["candidate_id"], r["latency_ms"]) for r in results) != Counter(
            (c, latency) for c in rules for latency in (500, 600, 1000)):
        raise ValueError("candidate/latency grid mismatch")
    rows = list(read_csv(run_dir / "shares5_base_release0/metrics.csv"))
    raw = {(r["candidate_id"], r["latency_ms"]): r for r in rows}
    daily = list(read_csv(report_dir / "daily.csv"))
    predictions = read(prediction_coverage)
    admission = read(admission_audit)
    if not admission["actual_input_gate_pass"]:
        raise ValueError("actual input admission failed")
    prediction_days = set(predictions["model_prediction_dates"])
    confidence = []
    for r in results:
        k = r["candidate_id"], r["latency_ms"]
        days = [d for d in daily if (d["candidate_id"], d["latency_ms"]) == k
                and d["market_start_utc_date"] in prediction_days]
        seed = int(hashlib.sha256(f"{k[0]}:{k[1]}:prediction_days".encode()).hexdigest()[:16], 16)
        ci = day_bootstrap(days, samples=2000, seed=seed)
        confidence.append({"candidate_id": k[0], "latency_ms": k[1],
            "prediction_dates": sorted(prediction_days), "diagnostic": ci,
            "qualification": "Only two partial prediction dates, development-selected rules; not evidence of statistical significance."})
    confidence_by_key = {(r["candidate_id"], r["latency_ms"]): r for r in confidence}
    by_candidate = defaultdict(list)
    for r in results:
        by_candidate[r["candidate_id"]].append(r)
    positive = lambda r: r["execution_complete_on_recorded_scope"] and r["recorded_scope_net_pnl_usd"] is not None and r["recorded_scope_net_pnl_usd"] > 0
    top = sorted((r for r in results if r["latency_ms"] == 500),
                 key=lambda r: r["recorded_scope_net_pnl_usd"], reverse=True)[:3]

    def describe(candidate_id):
        runs = sorted((dict(r) for r in by_candidate[candidate_id]), key=lambda r: r["latency_ms"])
        for row in runs:
            row["program_cash_path_uncertainty_including_warmup"] = row["day_cluster_uncertainty"]
            row["day_cluster_uncertainty"] = confidence_by_key[(candidate_id, row["latency_ms"])]["diagnostic"]
            row["day_cluster_uncertainty_basis"] = "Model prediction days only: Oct8/9; excludes Oct7 no-prediction warmup. Two partial days do not establish significance."
        return {"rule": rules[candidate_id], "latencies": runs,
                "daily": [r for r in daily if r["candidate_id"] == candidate_id],
                "prediction_day_uncertainty": [r for r in confidence if r["candidate_id"] == candidate_id],
                "feature_gate_diagnostics": [{"latency_ms": r["latency_ms"],
                    "valid_feature_rows": raw[(candidate_id, r["latency_ms"])]["valid_feature_rows"],
                    "invalid_decision_reasons": raw[(candidate_id, r["latency_ms"])]["invalid_decision_reasons"]} for r in runs]}

    mechanism_best = []
    for mechanism in sorted({r["mechanism"] for r in rules.values()}):
        candidates = [r for r in results if r["latency_ms"] == 500 and rules[r["candidate_id"]]["mechanism"] == mechanism]
        best = max(candidates, key=lambda r: r["recorded_scope_net_pnl_usd"])
        mechanism_best.append({"mechanism": mechanism, "descriptive_best_candidate": describe(best["candidate_id"]),
                               "positive_candidates_500ms": sum(positive(r) for r in candidates),
                               "registered_candidates": len(candidates)})
    overlap = read(report_dir / "execution_overlap.json")
    overlap_summary = []
    for latency in overlap["scenarios"][0]["latencies"]:
        overlap_summary.append({"latency_ms": latency["latency_ms"],
            "registered_candidates": latency["registered_candidates"],
            "distinct_observed_attempt_sequences": latency["distinct_nonempty_observed_attempt_sequences"],
            "duplicate_groups": [g["candidate_ids"] for g in latency["exact_nonempty_sequence_groups"] if len(g["candidate_ids"]) > 1]})
    coverage = read(run_dir / "coverage.json")
    value = {
        "schema": "completed-jpr-assessment-v2", "formal_oos_result": None, "qualified_strategies": 0,
        "new_backtests_by_this_diagnostic": 0, "new_strategy_registrations_by_this_diagnostic": 0,
        "completed_execution_scenarios": 288, "registered_rules_in_this_replay": 96,
        "historical_trial_denominator": "Preserve all prior screening and all 96 rules; replaying frozen rules is not registering 96 new independent strategies.",
        "source_run": str(run_dir.resolve()), "run_key": completion["run_key"],
        "completion_sha256": digest(run_dir / "completion.json"),
        "diagnostic_source_hashes": {str(Path(p)): digest(p) for p in (report_dir / "summary.json", report_dir / "daily.csv", report_dir / "execution_overlap.json", prediction_coverage, admission_audit)},
        "assessment_code_sha256": digest(__file__), "execution_status_counts": dict(Counter(r["engine_status"] for r in results)),
        "reconciliation_differences": summary["reconciliation_differences"],
        "coverage": {k: v for k, v in coverage.items() if k != "markets"},
        "saved_prediction_coverage": predictions,
        "raw_decisions": sum(r["counts"]["decision"] for r in admission["inputs"]),
        "decisions_without_saved_scores": sum(r["counts"]["decision"] - r["counts"]["decisions_with_score"] for r in admission["inputs"]),
        "missing_score_policy": "All decisions retained. Individual rule invalid_decision_reasons only counts eligibility checks before that market's first signal, not the global missing-score denominator.",
        "positive_recorded_scope_candidates_by_latency": {str(l): sum(positive(r) for r in results if r["latency_ms"] == l) for l in (500, 600, 1000)},
        "candidates_positive_in_all_three_latencies": sum(all(positive(r) for r in rs) for rs in by_candidate.values()),
        "complete_calendar_pnl_nonnull_rows": sum(r["net_pnl_usd"] is not None for r in results),
        "top_three_development_parameter_candidates_at_500ms": [describe(r["candidate_id"]) for r in top],
        "mechanism_descriptive_best": mechanism_best, "observed_execution_overlap": overlap_summary,
        "uncertainty_policy": "Main model day_cluster_uncertainty excludes Oct7 no-prediction warmup and uses Oct8/9 only. The unchanged generic summary and separately preserved program_cash_path_uncertainty_including_warmup use all three observed dates. Neither few-cluster bootstrap accounts for prior selection, serial dependence or missing full days.",
        "uncertainty_correction_provenance": {
            "prior_assessment_sha256": digest(report_dir / "assessment.json") if (report_dir / "assessment.json").exists() else None,
            "unchanged_generic_summary_sha256": digest(report_dir / "summary.json"),
            "prediction_coverage_sha256": digest(prediction_coverage),
            "old_main_clusters": 3, "corrected_main_clusters": 2,
            "excluded_warmup_dates": ["2026-10-07"], "included_prediction_dates": sorted(prediction_days),
            "pnl_or_ledger_modified": False, "replay_reexecuted": False,
            "reason": "Oct7 has no saved model predictions, so it is not an evaluable model-return day. All-date input/coverage denominators remain unchanged."},
        "execution_scope": "Each candidate/latency has its own $1000 account; single taker FAK leg at five requested shares, fixed signal-time limit, held to Gamma official settlement. No maker, hedge, scale-in or shared multi-strategy portfolio.",
        "capacity_scope": "Displayed-depth upper-bound simulation; current base scenario is five shares, full displayed depth, zero extra cost and zero delay after locally received official outcome. No real-fill or capacity verification.",
        "interpretation": "All reported gains concern observed development recordings only. Calendar PnL and formal OOS remain null. The top three are threshold variants of the same joint_5 exhaustion mechanism and cannot be counted as three independent edges."
    }
    (report_dir / "assessment.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (report_dir / "prediction_day_uncertainty.json").write_text(json.dumps({"schema":"prediction-day-descriptive-uncertainty-v1", "formal_oos_result":None, "results":confidence}, indent=2, allow_nan=False) + "\n")
    (report_dir / "program_cash_path_uncertainty_including_warmup.json").write_text(json.dumps({
        "schema":"program-cash-path-uncertainty-not-model-evaluation-v1",
        "source_summary_sha256":digest(report_dir / "summary.json"),
        "interpretation":"Preserved generic three-observed-date diagnostic including Oct7 warmup; not the main model-return CI.",
        "results":[{"candidate_id":r["candidate_id"], "latency_ms":r["latency_ms"],
                    "diagnostic":r["day_cluster_uncertainty"]} for r in results]},indent=2,allow_nan=False)+"\n")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--prediction-coverage", required=True)
    parser.add_argument("--admission-audit", required=True)
    args = parser.parse_args()
    result = assess(args.run_dir, args.report_dir, args.prediction_coverage, args.admission_audit)
    print(json.dumps({"output":str(Path(args.report_dir) / "assessment.json"), "scenarios":result["completed_execution_scenarios"], "reconciliation_differences":result["reconciliation_differences"], "new_backtests":0}))
