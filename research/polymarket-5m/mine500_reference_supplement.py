"""Complete the missing EXISTING M500-501..720 reference replay only.

The prior deep batch's reference call used make_candidates(), which returns
500 rules. This separate runner evaluates the 220 already registered supplement
rules; it never repeats M500-001..500 or treats the 220 as newly mined trials.
It consumes the already validated, unenriched combined development tape and
streams it through the unchanged v2 executor, default 500/600/1000 ms, 5 shares,
and $1000 per independent candidate/latency ledger. No model is fit here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import mine500_catalog as catalog
import mine500_constraints as constraints
import mine500_replay_v2 as execution


VERSION = "mine500-existing-supplement-reference-v1"
CONSUMED_DAYS = frozenset(("2026-10-07", "2026-10-08", "2026-10-09"))


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n")


def write_attempts(path, rows):
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            for row in rows:
                handle.write((json.dumps(row, separators=(",", ":"), allow_nan=False)+"\n").encode())


def validate_supplement_registry(candidates):
    expected = [f"M500-{i:03d}" for i in range(501, 721)]
    ids = [c["id"] for c in candidates]
    if ids != expected:
        raise ValueError("supplement must be exactly ordered M500-501..720, with no original 500")
    if len({c["rule_fingerprint"] for c in candidates}) != len(candidates):
        raise ValueError("duplicate supplementary rule fingerprint")
    return ids


def validate_summary_coverage(candidates, summaries, latencies_ms):
    """Derive actual counts from executed IDs; reject a hardcoded 720 claim.

    This generic helper can be used by other runners. It does not assume either
    500 or 720 candidates and verifies every (candidate, latency) exactly once.
    Blocked/no-signal rules still require summary rows; no trades are fabricated.
    """
    ids = [c["id"] for c in candidates]
    lags = list(latencies_ms)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be nonempty and unique")
    if not lags or len(lags) != len(set(lags)) or any(type(x) is not int or x <= 0 for x in lags):
        raise ValueError("latencies must be positive, unique integers")
    expected = {(cid, lag) for cid in ids for lag in lags}
    actual = Counter((r["candidate_id"], r["latency_ms"]) for r in summaries)
    duplicates = [key for key, count in actual.items() if count != 1]
    missing, unexpected = expected-set(actual), set(actual)-expected
    if duplicates or missing or unexpected:
        raise ValueError(f"reference summary coverage mismatch: duplicate={len(duplicates)}, "
                         f"missing={len(missing)}, unexpected={len(unexpected)}")
    return {"actual_candidate_count": len(ids), "actual_latency_count": len(lags),
            "actual_summary_rows": len(summaries), "expected_summary_rows": len(expected),
            "actual_candidate_ids": sorted(ids), "actual_latencies_ms": sorted(lags),
            "complete_candidate_latency_coverage": True}


def checked_events(path, audit):
    previous = -math.inf
    header_seen = False
    counts = Counter()
    for index, event in enumerate(execution.read_jsonl(path)):
        now = event.get("recv_ms")
        if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now) or now < previous:
            raise ValueError("invalid or regressed input receipt clock")
        previous = now
        if index == 0:
            if event.get("type") != "dataset" or event.get("split") != "development":
                raise ValueError("expected combined consumed-development dataset header")
            if (event.get("book_resolution") != "decision_and_match_projection"
                    or set(event.get("supported_latencies_ms", [])) != {500, 600, 1000}
                    or event.get("decision_grid_ms") != 1000):
                raise ValueError("incompatible development projection")
            audit["dataset"] = event
            header_seen = True
        elif event.get("type") == "dataset":
            raise ValueError("embedded second dataset header")
        if event.get("type") == "decision":
            day = datetime.fromtimestamp(now/1000, timezone.utc).date().isoformat()
            if day not in CONSUMED_DAYS:
                raise ValueError("unconsumed development date")
            if any(name.startswith(("prob_", "path_", "tree_")) for name in event.get("features", {})):
                raise ValueError("expected original unenriched combined tape")
        counts[event.get("type", "missing_type")] += 1
        yield event
    if not header_seen:
        raise ValueError("empty combined input")
    audit["record_counts"] = dict(counts)
    audit["stream_fully_consumed"] = True
    audit["gzip_crc_validated_by_standard_full_read"] = str(path).endswith(".gz")


def outcome_bounds(rows):
    settled = [r for r in rows if r.get("settled") and r["filled_shares"] > 0]
    opened = [r for r in rows if not r.get("settled") and r["filled_shares"] > 0]
    realized = math.fsum(r["net_pnl_usd"] for r in settled)
    cost = math.fsum(r["cost_usd"] for r in opened)
    shares = math.fsum(r["filled_shares"] for r in opened)
    return {"settled_pnl_usd": realized, "unresolved_filled_orders": len(opened),
            "unresolved_cost_usd": cost, "unresolved_shares": shares,
            "settlement_pnl_lower_bound_usd": realized-cost,
            "settlement_pnl_upper_bound_usd": realized+shares-cost,
            "bounds_scope": "recorded simulated fills only; no bounds for incomplete match horizons"}


def summarize_daily(result):
    groups, daily = defaultdict(list), defaultdict(list)
    for row in result["attempts"]:
        key = row["candidate_id"], row["latency_ms"]
        groups[key].append(row)
        day = datetime.fromtimestamp(row["decision_ms"]/1000, timezone.utc).date().isoformat()
        daily[(*key, day)].append(row)
    for summary in result["summary"]:
        summary.update(outcome_bounds(groups[summary["candidate_id"], summary["latency_ms"]]))
    return [{"candidate_id": cid, "latency_ms": lag, "day_utc": day,
             "attempts": len(rows), "submitted": sum(r["submitted"] for r in rows),
             "full_fills": sum(r["status"] == "filled_full" for r in rows),
             "partial_fills": sum(r["status"] == "filled_partial" for r in rows),
             "filled_shares": math.fsum(r["filled_shares"] for r in rows),
             "fees_usd": math.fsum(r["fees_usd"] for r in rows),
             "status_counts": dict(Counter(r["status"] for r in rows)), **outcome_bounds(rows)}
            for (cid, lag, day), rows in sorted(daily.items())]


def code_hashes():
    modules = [Path(__file__), Path(catalog.__file__), Path(execution.__file__), Path(constraints.__file__)]
    return {path.name: digest(path) for path in modules}


def git_head():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                            text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def run(input_path, output):
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    candidates = catalog.make_supplementary_candidates()
    ids = validate_supplement_registry(candidates)
    config = execution.ReplayConfig()
    # Refuse any accidental default drift instead of silently replaying a new specification.
    if config.latencies_ms != (500, 600, 1000) or config.shares != 5 or config.initial_cash_usd != 1000:
        raise ValueError("v2 default reference execution specification changed")
    output.mkdir(parents=True, exist_ok=False)
    registration = {"schema": VERSION, "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head(), "python_version": sys.version,
        "input_path": str(input_path), "input_sha256": digest(input_path), "code_hashes": code_hashes(),
        "candidates": candidates, "candidate_ids": ids, "actual_registered_candidates": len(ids),
        "expected_scenario_count": len(ids)*len(config.latencies_ms), "execution_config": asdict(config),
        "new_trials": 0, "existing_trials_replayed": len(ids), "not_new_trials": True,
        "reference_scope": "only_missing_existing_M500_501_to_720",
        "original_500_will_be_replayed": False,
        "evidence_class": "reused_development_reference_transport_diagnostic",
        "historical_trial_count_increment": 0,
        "reason": "prior make_candidates call evaluated only original500 despite reference720 filenames",
        "consumed_decision_days": sorted(CONSUMED_DAYS), "formal_oos_result": None,
        "existing_holdout_protocols_modified": False}
    write_json(output/"registration.json", registration)
    print(f"registered {len(ids)} existing supplement rules; zero new trials", flush=True)
    input_audit = {}
    result = execution.replay_events(checked_events(input_path, input_audit), candidates, catalog.signal, config)
    if input_audit.get("stream_fully_consumed") is not True:
        raise ValueError("executor did not consume complete input")
    coverage = validate_summary_coverage(candidates, result["summary"], config.latencies_ms)
    unexpected_attempt_ids = {r["candidate_id"] for r in result["attempts"]}-set(ids)
    if unexpected_attempt_ids:
        raise ValueError("attempt from original500 or unregistered candidate")
    daily = summarize_daily(result)
    write_json(output/"input_audit.json", input_audit)
    write_json(output/"coverage_audit.json", coverage)
    write_json(output/"daily.json", daily)
    attempt_count = len(result["attempts"])
    write_attempts(output/"attempts.jsonl.gz", result.pop("attempts"))
    result.update(reference_scope=registration["reference_scope"], actual_candidate_count=len(ids),
                  actual_scenario_count=coverage["actual_summary_rows"], new_trials=0,
                  not_new_trials=True, formal_oos_result=None)
    write_json(output/"replay.json", result)
    changed_input = digest(input_path) != registration["input_sha256"]
    changed_code = code_hashes() != registration["code_hashes"]
    if changed_input or changed_code:
        write_json(output/"invalidated_run.json", {"changed_input": changed_input,
                   "changed_code": changed_code, "formal_oos_result": None})
        raise ValueError("registered code or input changed during supplement replay")
    completion = {"completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "actual_existing_candidates_replayed": coverage["actual_candidate_count"],
        "actual_candidate_scenarios": coverage["actual_summary_rows"], "attempt_count": attempt_count,
        "new_trials": 0, "not_new_trials": True, "original500_replayed": False,
        "simulations_only": True, "formal_oos_result": None, "confirmed_strategies": 0,
        "output_sha256": {name:digest(output/name) for name in
            ("registration.json", "input_audit.json", "coverage_audit.json", "daily.json", "attempts.jsonl.gz", "replay.json")}}
    write_json(output/"completion.json", completion)
    print(json.dumps(completion), flush=True)
    return completion


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    run(args.input, args.out)
