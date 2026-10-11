"""Twelve preregistered nonlinear DEVELOPMENT trials; no holdout execution.

Only previously consumed October 7/8/9 tapes are accepted. Four execution
scenarios and three total latencies give 144 alternatives, never a portfolio
sum or 144 independent trials. No network, orders or production modifications.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
from itertools import zip_longest
import math
from pathlib import Path

import mine500_deep_batch as common
import mine500_replay_v2 as execution
import mine500_tree_models as tree
from mine500_statistics import analyze

VERSION = "mine500-tree-development-v1"
CONSUMED_DAYS = ("2026-10-07", "2026-10-08", "2026-10-09")
SCENARIOS = (
    ("size5", {"shares": 5.}),
    ("size20", {"shares": 20.}),
    ("capacity500", {"shares": 500.}),
    ("extra_1c", {"shares": 5., "extra_cost_per_filled_share_usd": .01}),
)
DEPENDENCY_NAMES = (
    "mine500_tree_batch.py", "mine500_tree_models.py", "mine500_deep_batch.py",
    "mine500_probability_models.py", "mine500_path_candidates.py", "mine500_catalog.py",
    "mine500_replay_v2.py", "mine500_constraints.py", "mine500_statistics.py",
    "mine500_shared_portfolio.py", "mine500_constraint_extract.py", "mine500_adapter.py",
)


def code_hashes():
    return {name: common.digest(Path(__file__).with_name(name)) for name in DEPENDENCY_NAMES}


def dependency_versions():
    result = {}
    for name in ("numpy", "scipy", "scikit-learn"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def validate_scores(events, scores, candidates):
    """Missing fits stay missing; malformed, misjoined or future scores abort."""
    if not isinstance(scores, dict):
        raise ValueError("score map must be a dictionary")
    decisions = {(str(e["market_id"]), e["recv_ms"]) for e in events if e["type"] == "decision"}
    models = {c["model_id"] for c in candidates}
    for key, value in scores.items():
        if (not isinstance(key, tuple) or len(key) != 3 or key[:2] not in decisions
                or key[2] not in models or not isinstance(value, dict)):
            raise ValueError("score does not join an eligible decision and model")
        p, fit, available = (value.get(name) for name in
                            ("p_up", "model_fit_ms", "feature_available_ms"))
        if (not all(_finite(x) for x in (p, fit, available)) or not 0 <= p <= 1
                or fit > key[1] or available > key[1]):
            raise ValueError("invalid or future model score")
    return {"scored_model_decisions": len(scores), "decision_rows": len(decisions),
            "missing_model_decisions": len(decisions) * len(models) - len(scores),
            "missing_score_policy": "remain missing; never impute zero or midpoint"}


def validate_enrichment(events, enriched):
    if len(events) != len(enriched):
        raise ValueError("score attachment changed event count")
    sentinel = object()
    observed = 0
    for original, attached in zip_longest(events, enriched, fillvalue=sentinel):
        if original is sentinel or attached is sentinel:
            raise ValueError("score attachment changed actual event count")
        observed += 1
        if original["type"] != "decision":
            if original != attached:
                raise ValueError("score attachment changed a nondecision event")
        elif any(attached.get(k) != v for k, v in original.items()
                 if k not in ("features", "feature_available_ms")):
            raise ValueError("score attachment changed decision identity")
    if observed != len(events):
        raise ValueError("materialized tape count disagrees with actual events")


def materialize_enrichment(events, scores, path):
    """Persist one event at a time, then validate by two bounded-memory readers."""
    count = common.write_gzip(path, tree.attach_scores(events, scores))
    enriched = common.ReplayTape(path, count)
    validate_enrichment(events, enriched)
    return enriched


def write_metrics_csv(filename, rows):
    # Nested status and execution detail remains lossless in the JSON companion.
    fields = sorted({k for r in rows for k, v in r.items() if not isinstance(v, (dict, list, tuple))})
    with Path(filename).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(inputs, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    combined_path = output / "combined_input.jsonl.gz"
    events, input_audit = common.combine_inputs(inputs, materialize_output=combined_path)
    candidates = tree.make_candidates()
    if (len(candidates) != 12 or len({c["id"] for c in candidates}) != 12
            or any(not c["id"].startswith("MPT") for c in candidates)):
        raise ValueError("expected twelve distinct registered MPT candidates")
    registration = {
        "schema": VERSION, "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates, "new_trials": 12, "existing_mine500_trials": 760,
        "cumulative_mine500_trial_lower_bound": 772,
        "known_multiplicity_lower_bound_including_zoo100": 872,
        "other_historical_adaptive_trials": "additional; not erased or fully enumerated",
        "independent_economic_mechanisms_claimed": 0,
        "scenarios": [{"name": name, "config": asdict(execution.ReplayConfig(**params))}
                      for name, params in SCENARIOS],
        "input_hashes": input_audit["input_hashes"], "code_hashes": code_hashes(),
        "dependency_versions": dependency_versions(), "consumed_decision_days": CONSUMED_DAYS,
        "event_storage": "reentrant_gzip_disk_tapes_without_full_event_lists",
        "combined_tape": {"path": str(combined_path.resolve()), "events": len(events),
                          "sha256": common.digest(combined_path)},
        "formal_oos_results": None, "existing_holdout_protocols_modified": False,
        "shared_panel_policy": "earliest signal among all twelve from uncensored intents; candidate ID breaks ties; failed first consumes market; shared $1000 ledger",
        "intent_generation_cash_ceiling_usd": 1e12,
        "actual_shared_panel_cash_usd": 1000.,
        "limitations": ["Reused development forward folds are not independent OOS.",
            "Displayed depth gives counterfactual capacity bounds, not real fills.",
            "Size, cost and latency scenarios are dependent alternatives and cannot be added.",
            "Three UTC dates do not support day-cluster significance or qualification."]}
    common.write_json(output / "registration.json", registration)
    common.write_json(output / "input_audit.json", input_audit)
    print("registered twelve new development trials before fitting", flush=True)
    folds, scores = tree.train_forward_days(events, consumed_days=CONSUMED_DAYS,
                                            input_hashes=input_audit["input_hashes"])
    common.write_json(output / "fit_folds.json", folds)
    common.write_json(output / "score_audit.json", validate_scores(events, scores, candidates))
    enriched_path = output / "enriched_development_events.jsonl.gz"
    enriched = materialize_enrichment(events, scores, enriched_path)
    materialized_hashes = {str(combined_path.resolve()): registration["combined_tape"]["sha256"],
                          str(enriched_path.resolve()): common.digest(enriched_path)}
    common.write_json(output / "materialization_audit.json", {
        "storage": "streamed_reentrant_gzip_tapes", "combined_events": len(events),
        "enriched_events": len(enriched), "sha256": materialized_hashes,
        "event_identity_and_nondecision_content_verified": True})
    del scores, folds
    metrics, daily, panels = [], [], []
    for name, params in SCENARIOS:
        print("tree replay: " + name, flush=True)
        result = execution.replay_events(enriched, candidates, tree.signal, execution.ReplayConfig(**params))
        daily.extend(dict(row, scenario=name) for row in common.describe(result))
        panel, intent_doc = common.panel_intent_results(
            enriched, candidates, tree.signal, execution.ReplayConfig(**params))
        common.write_gzip(output / (name + "_panel_intent_attempts.jsonl.gz"), intent_doc.pop("attempts"))
        common.write_json(output / (name + "_panel_intent_replay.json"), intent_doc)
        for row in panel:
            if row.get("status") == "simulated_shared_cash":
                row["policy"] = registration["shared_panel_policy"]
        panels.append({"scenario": name, "results": panel})
        stats = common.correct_current_coverage_denominator(analyze(result, family_tests=872), 12, 872)
        common.write_json(output / (name + "_statistics.json"), stats)
        common.write_gzip(output / (name + "_attempts.jsonl.gz"), result.pop("attempts"))
        common.write_json(output / (name + "_replay.json"), result)
        metrics.extend(dict(row, scenario=name) for row in result["summary"])
    common.write_json(output / "new_candidate_metrics.json", metrics)
    write_metrics_csv(output / "new_candidate_metrics.csv", metrics)
    common.write_json(output / "new_candidate_daily.json", daily)
    common.write_json(output / "shared_cash_panels.json", panels)
    current_inputs = {filename: common.digest(filename) for filename in input_audit["input_hashes"]}
    if (code_hashes() != registration["code_hashes"] or current_inputs != registration["input_hashes"]
            or dependency_versions() != registration["dependency_versions"]
            or any(common.digest(path) != sha for path, sha in materialized_hashes.items())):
        common.write_json(output / "invalidated_run.json", {"reason": "registered_code_input_or_dependency_changed",
                                                              "formal_oos_result": None})
        raise ValueError("registered code, input or dependency changed during execution")
    if len(metrics) != 144:
        raise ValueError("incomplete scenario accounting")
    common.write_json(output / "completion.json", {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(), "new_registered_trials": 12,
        "new_candidate_scenarios": len(metrics), "simulations_only": True,
        "formal_oos_result": None, "confirmed_strategies": 0,
        "observed_hours": input_audit["dataset"]["observed_hours"],
        "code_and_input_integrity_verified": True})
    print("tree development batch completed; no OOS qualification", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    run(args.inputs, args.out)
