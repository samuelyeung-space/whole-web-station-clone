"""Reuse the selected EPM scores at fixed 100-share orders and USD 1,000.

This is a DEVELOPMENT capacity replay, not an OOS evaluator. It never fits a
model, tunes a rule, changes first signals, retries an unsuccessful market or
multiplies a smaller-order PnL. All nine execution alternatives are registered
before replay and use their own continuous cross-market/day cash account.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import mine500_deep_batch as batch
import mine500_endpoint_model as model
import mine500_probability_models as probability
import mine500_replay_v2 as execution
from mine500_reference_supplement import validate_summary_coverage

VERSION = "fixed100-epm-existing-scores-development-v1"
CANDIDATE_ID = "EPM-matched_market-02"
MODEL_ID = "matched_market"
SCORE_KEY = "ep_prob_matched_market"
SCENARIOS = (
    ("fixed100", {}),
    ("fixed100_extra_1c", {"extra_cost_per_filled_share_usd": .01}),
    ("fixed100_half_depth", {"depth_fraction": .5}),
)


def selected_candidate(registration):
    selected = [r for r in registration["candidates"] if r["id"] == CANDIDATE_ID]
    expected = [r for r in model.candidates() if r["id"] == CANDIDATE_ID]
    if selected != expected or len(selected) != 1:
        raise ValueError("selected candidate differs from the original fixed rule")
    return selected[0]


def checked_scores(scores_path, model_audit):
    folds = {}
    for fold in model_audit["folds"]:
        if fold["model_id"] != MODEL_ID or fold["status"] != "scored":
            continue
        if probability._hash([MODEL_ID, fold["train_hash"], fold["learned_state"]]) != fold["model_hash"]:
            raise ValueError("model learned-state hash mismatch")
        if fold["latest_training_label_ms"] >= fold["cutoff_ms"]:
            raise ValueError("future training label")
        folds[fold["day"]] = fold
    scores = {}
    for row in execution.read_jsonl(scores_path):
        if row["model_id"] != MODEL_ID:
            continue
        key = (str(row["market_id"]), row["recv_ms"])
        if key in scores:
            raise ValueError("duplicate model score")
        fold = folds.get(probability._day(row["recv_ms"]))
        if not fold or row["model_hash"] != fold["model_hash"] or row["model_fit_ms"] != fold["cutoff_ms"]:
            raise ValueError("score does not reference its original prior-day model")
        if key[0] in fold["training_market_ids"]:
            raise ValueError("training market reused for prediction")
        if max(row["model_fit_ms"], row["feature_available_ms"]) > row["recv_ms"]:
            raise ValueError("future model or feature")
        if not probability._finite(row["p_up"]) or not 0 < row["p_up"] < 1:
            raise ValueError("invalid score probability")
        scores[key] = row
    return scores, folds


def validate_enriched(path, scores, consumed_days):
    counts, seen, score_seen = Counter(), set(), set()
    header = None
    for row in execution.read_jsonl(path):
        counts[row["type"]] += 1
        if row["type"] == "dataset":
            if header is not None or row.get("split") != "development":
                raise ValueError("only one consumed development header allowed")
            header = row
        if row["type"] != "decision":
            continue
        if probability._day(row["recv_ms"]) not in consumed_days:
            raise ValueError("unconsumed date cannot enter development replay")
        key = (str(row["market_id"]), row["recv_ms"])
        if key in seen:
            raise ValueError("duplicate input decision")
        seen.add(key)
        expected = scores.get(key)
        p = row["features"].get(SCORE_KEY)
        availability = row["feature_available_ms"].get(SCORE_KEY)
        if expected is None:
            if p is not None or availability is not None:
                raise ValueError("unexpected fitted score in input")
        else:
            expected_time = max(expected["model_fit_ms"], expected["feature_available_ms"])
            if p != expected["p_up"] or availability != expected_time:
                raise ValueError("enriched score differs from original saved score")
            score_seen.add(key)
    if score_seen != set(scores):
        raise ValueError("original score missing from input")
    if not header or not header.get("observed_intervals_ms"):
        raise ValueError("missing observed recording intervals")
    return dict(record_counts=dict(counts), checked_selected_scores=len(score_seen),
                dataset=header, count=sum(counts.values()))


def audit_result(result, reference):
    """Arithmetic and unchanged first-signal checks, independent of summary totals."""
    rows = result["attempts"]
    by_latency = {}
    for latency in result["config"]["latencies_ms"]:
        subset = [r for r in rows if r["latency_ms"] == latency]
        keys = [(r["market_id"], r["decision_ms"], r["side"], r["limit_price"]) for r in subset]
        if keys != reference[latency]:
            raise ValueError("first signal or fixed limit changed from the 5-share rule")
        if any(r["requested_shares"] != 100. or r["match_ms"] - r["decision_ms"] != latency for r in subset):
            raise ValueError("order sizing or total latency mismatch")
        if any(not 0 <= r["filled_shares"] <= 100.000000001 for r in subset):
            raise ValueError("filled more than fixed quantity")
        settled = [r for r in subset if r["settled"]]
        pnl = math.fsum(r["payout_usd"] - r["cost_usd"] for r in settled)
        cash = 1000. + math.fsum((r["payout_usd"] or 0.) - r["cost_usd"] for r in subset)
        summary = next(r for r in result["summary"] if r["latency_ms"] == latency)
        if abs(pnl - summary["realized_pnl_usd"]) > 1e-7 or abs(cash - summary["ending_cash_usd"]) > 1e-7:
            raise ValueError("cash or profit reconciliation failure")
        if summary["min_available_cash_usd"] < -1e-7:
            raise ValueError("cash overdrawn")
        by_latency[latency] = dict(first_signal_and_limit_equal_original=True,
            independent_pnl_sum=pnl, independent_ending_cash=cash,
            cash_rejections=sum(r["status"] == "rejected_insufficient_available_cash" for r in subset),
            all_requested_exactly_100=True,
            all_matched_at_registered_total_latency=True)
    return dict(arithmetic_discrepancies=0, by_latency=by_latency)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root", type=Path, required=True,
                    help="Original run12 result folder including MANIFEST.json")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    src, out = args.source_root.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    original = src / "endpoint_models"
    paths = {name: original / name for name in (
        "enriched.jsonl.gz", "scores.jsonl.gz", "model_audit.json", "registration.json", "size5_attempts.jsonl.gz")}
    hashes = {name: batch.digest(path) for name, path in paths.items()}
    manifest = {r["path"]: r for r in json.loads((src / "MANIFEST.json").read_text())}
    # The compressed enriched tape is reproducible and was omitted from the old
    # package; authenticate its scores separately and hash the full input here.
    for name in paths:
        if name == "enriched.jsonl.gz":
            continue
        if manifest["endpoint_models/" + name]["sha256"] != hashes[name]:
            raise ValueError("original artifact manifest hash mismatch: " + name)
    candidate = selected_candidate(json.loads(paths["registration.json"].read_text()))
    code = Path(__file__).parent
    old_code = json.loads((src / "code_identity.json").read_text())["hashes"]
    code_hashes = {name: batch.digest(code / name) for name in old_code}
    if code_hashes != old_code:
        raise ValueError("run12 code identity changed")
    code_hashes[Path(__file__).name] = batch.digest(__file__)
    configs = [(name, execution.ReplayConfig(shares=100., initial_cash_usd=1000.,
                require_constraints=True, **changes)) for name, changes in SCENARIOS]
    batch.write_json(out / "registration.json", dict(version=VERSION,
        registered_at_utc=datetime.now(timezone.utc).isoformat(), candidate=candidate,
        effective_split="development", formal_oos_result=None, refit=False,
        input_files={name: dict(path=str(path), sha256=hashes[name]) for name, path in paths.items()},
        code_sha256=code_hashes, alternatives=[dict(name=n, config=asdict(c)) for n,c in configs],
        trial_scope="one existing adaptively selected candidate; nine new capacity alternatives; not nine strategies",
        known_historical_trial_lower_bound=901,
        cash_scope="one continuous 1000 USD cash account across markets and days for each alternative; alternatives cannot be summed",
        quantity_policy="request exactly 100 at unchanged first signal and best ask limit; allow partial FAK; reject full order if cash reservation unavailable; no resizing or retry",
        inference="displayed depth simulation upper bound; no actual fill claim; selected after examining old development returns"))
    scores, folds = checked_scores(paths["scores.jsonl.gz"], json.loads(paths["model_audit.json"].read_text()))
    input_audit = validate_enriched(paths["enriched.jsonl.gz"], scores, batch.CONSUMED_DAYS)
    input_audit["model_hashes"] = {day: f["model_hash"] for day,f in folds.items()}
    batch.write_json(out / "input_audit.json", input_audit)
    reference = {latency: [] for latency in (500,600,1000)}
    for row in execution.read_jsonl(paths["size5_attempts.jsonl.gz"]):
        if row["candidate_id"] == CANDIDATE_ID:
            reference[row["latency_ms"]].append((row["market_id"],row["decision_ms"],row["side"],row["limit_price"]))
    tape = batch.ReplayTape(paths["enriched.jsonl.gz"], input_audit["count"])
    metrics, daily = [], []
    for name, config in configs:
        result = execution.replay_events(tape, [candidate], model.signal, config)
        validate_summary_coverage([candidate], result["summary"], config.latencies_ms)
        reconciliation = audit_result(result, reference)
        daily.extend(dict(row, scenario=name) for row in batch.describe(result))
        metrics.extend(dict(row, scenario=name) for row in result["summary"])
        batch.write_gzip(out / (name + "_attempts.jsonl.gz"), result["attempts"])
        batch.write_json(out / (name + "_audit.json"), {k:v for k,v in result.items() if k != "attempts"})
        batch.write_json(out / (name + "_reconciliation.json"), reconciliation)
        print(name + " completed", flush=True)
    batch.write_json(out / "metrics.json", metrics)
    batch.write_json(out / "daily.json", daily)
    batch.write_json(out / "completion.json", dict(version=VERSION, alternatives=len(metrics),
        candidate_count=1, actual_replay_alternatives=9, formal_oos_result=None, confirmed_strategies=0,
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
        drawdown_basis="realized-only after official outcome receipt; not mark-to-market",
        sizing_change_only=True, scores_refitted=False, first_signals_and_limits_match_size5=True))


if __name__ == "__main__":
    main()
