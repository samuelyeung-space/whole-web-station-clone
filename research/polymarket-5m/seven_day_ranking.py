"""Fail-closed admission to a common-window, independent-account development rank.

No replay, fitting, order submission, OOS promotion, or implicit window selection.
Protocol schema rank7-protocol-v1 (all fields mandatory)::

 {"schema":"rank7-protocol-v1", "registered_at_utc":"...Z",
  "window_start_utc":"...T00:00:00Z", "window_end_utc":"...T00:00:00Z",
  "candidate_sha256":"<hash of ordered candidate_rules>",
  "strategy_identity_sha256":"<hash of registration.strategies>",
  "code_sha256":{"replay_lab.py":"...", ...}, "cache_key":"...",
  "events_sha256":"...", "market_universe":{"asset":"BTC",
  "interval_seconds":300,"market_ids":["all 2016 pinned IDs"]},
  "benchmark":{"initial_cash_usd":1000,"requested_shares":100,
  "primary_latency_ms":500,"latencies_ms":[500,600,1000],
  "max_quote_age_ms":250,"cash_release_delay_ms":0,"require_constraints":true}}

Hashes use replay_lab.fingerprint (sorted compact JSON, ensure_ascii=True).
The caller must durably freeze the protocol before replay. Timestamp comparison
is an audit check, not proof of an externally witnessed freeze or untouched OOS.
Market IDs are an externally verified BTC universe; generic five-minute records
alone cannot prove their asset. Presence/span coverage is not feed-health proof.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

DAY = 86_400_000
SLOT = 300_000
STRESSES = {"base": (1, 0), "half_depth": (.5, 0), "plus_1cent": (1, .01)}
IDENTITY_KEYS = ("schema", "cache_key", "events_sha256", "strategies", "mode",
                 "priority", "scenarios", "code_sha256", "require_full_days", "formal_oos_result")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def read(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=_object,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON: " + x)))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def utc(value, midnight=False):
    require(isinstance(value, str), "explicit UTC timestamp required")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(date.utcoffset() == timedelta(0), "explicit UTC timestamp required")
    if midnight:
        require((date.hour, date.minute, date.second, date.microsecond) == (0, 0, 0, 0),
                "window boundaries must be UTC midnight")
    return date


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _assess(protocol, root):
    from replay_lab import verify_report_integrity
    require(protocol.get("schema") == "rank7-protocol-v1", "unsupported protocol schema")
    start_date, end_date = utc(protocol["window_start_utc"], True), utc(protocol["window_end_utc"], True)
    require(end_date - start_date == timedelta(days=7), "window must be exactly seven consecutive UTC days")
    start, end = int(start_date.timestamp() * 1000), int(end_date.timestamp() * 1000)
    benchmark = protocol["benchmark"]
    required = {"initial_cash_usd": 1000, "requested_shares": 100, "primary_latency_ms": 500,
                "latencies_ms": [500, 600, 1000], "max_quote_age_ms": 250, "require_constraints": True}
    require(all(benchmark.get(k) == v for k, v in required.items()), "benchmark must be independent $1000/target100, native constraints, 500/600/1000ms")
    delay = benchmark["cash_release_delay_ms"]
    require(type(delay) is int and delay >= 0, "fixed nonnegative cash release delay required")
    registration, completion = read(root / "registration.json"), read(root / "completion.json")
    require(registration["mode"] == completion["mode"] == "isolated", "shared results cannot enter individual ranking")
    require(utc(protocol["registered_at_utc"]) <= utc(registration["registered_at_utc"]), "protocol was registered after replay")
    require(completion["status"] == "completed_development_simulation", "run is not completed development simulation")
    require(registration["input_data_role"] == "consumed_development" and registration["model_fitting"] is False,
            "this gate only admits explicit no-fitting development replays")
    require(registration.get("formal_oos_result") is None and completion.get("formal_oos_result") is None,
            "development gate cannot accept OOS promotion")
    require(fingerprint({k: registration[k] for k in IDENTITY_KEYS}) == registration["run_key"] == completion["run_key"], "run identity mismatch")
    strategies = registration["strategies"]
    rules = strategies["candidate_rules"]
    candidates = [r["id"] for r in rules]
    require(candidates and len(candidates) == len(set(candidates)), "unique candidate IDs required")
    require(fingerprint(rules) == strategies["candidate_sha256"] == protocol["candidate_sha256"], "candidate rule fingerprint mismatch")
    require(fingerprint(strategies) == protocol["strategy_identity_sha256"], "strategy code/dependency identity mismatch")
    for key in ("code_sha256", "cache_key", "events_sha256"):
        require(protocol[key] == registration[key], "pinned " + key + " mismatch")
    code = protocol["code_sha256"]
    require(isinstance(code, dict) and {"replay_lab.py", "replay_dataset.py", "replay_runner.py",
            "replay_engine.py", "replay_reporting.py", "mine500_constraints.py"} <= set(code), "all execution code hashes required")
    for digest in list(code.values()) + [protocol[k] for k in ("cache_key", "events_sha256", "candidate_sha256", "strategy_identity_sha256")]:
        require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest), "valid SHA256 identities required")
    hashes = completion["output_sha256"]
    scenarios = registration["scenarios"]
    expected_files = {"registration.json", "metrics.json", "coverage.json"}
    for scenario in scenarios:
        name = scenario["name"]
        require(Path(name).name == name and name not in (".", ".."), "unsafe scenario path")
        expected_files.update(name + "/" + f for f in ("audit.json", "attempts.jsonl.gz", "metrics.csv", "daily.csv", "markets.csv", "cash_ledger.csv", "report.md"))
    require(expected_files <= set(hashes), "completion missing required output hashes")
    for name, digest in hashes.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path.is_file(), "unsafe or missing completed output: " + name)
        require(sha(path) == digest, "completed output hash mismatch: " + name)
    verify_report_integrity(root, scenarios, hashes)
    coverage = read(root / "coverage.json")
    days = coverage["daily"]
    wanted = {(start_date + timedelta(days=i)).date().isoformat() for i in range(7)}
    selected = [d for d in days if d.get("date_utc") in wanted]
    require(len(selected) == 7 and {d["date_utc"] for d in selected} == wanted, "missing/duplicate consecutive coverage days")
    for day in selected:
        require(day.get("observed_seconds") == 86400 and day.get("complete_span_slots") == 288
                and day.get("data_present_slots") == 288 and day.get("gaps_ms") == [],
                "incomplete daily recording/presence: " + day["date_utc"])
    universe = protocol["market_universe"]
    require(universe["asset"] == "BTC" and universe["interval_seconds"] == 300, "BTC5m universe required")
    pinned = universe["market_ids"]
    require(len(pinned) == len(set(pinned)) == 2016, "exactly 2016 unique pinned BTC market IDs required")
    markets = [m for m in coverage["markets"] if start <= m["start_ms"] < end]
    require(len(markets) == 2016 and {m["market_id"] for m in markets} == set(pinned), "window market universe mismatch")
    slots = set()
    for market in markets:
        at = market["start_ms"]
        require(finite(at) and at % SLOT == 0 and market["end_ms"] == at + SLOT
                and market.get("label_source") in ("polymarket", "gamma", "chainlink"), "unaligned market or unofficial label source")
        slots.add(at)
    require(slots == set(range(start, end, SLOT)), "duplicate or missing UTC market slots")
    scenario_stress = {}
    for scenario in scenarios:
        config = scenario["config"]
        expected = {"shares": 100, "initial_cash_usd": 1000, "latencies_ms": [500, 600, 1000],
                    "max_quote_age_ms": 250, "require_constraints": True, "cash_release_delay_ms": delay,
                    "full_or_zero": False, "side_specific_books": False, "min_notional_usd": 0}
        require(all(config.get(k) == v for k, v in expected.items()), "incompatible execution configuration")
        stress = [n for n, pair in STRESSES.items() if (config.get("depth_fraction"), config.get("extra_cost_per_filled_share_usd")) == pair]
        require(len(stress) == 1 and stress[0] not in scenario_stress.values(), "unknown/duplicate cost or depth stress")
        scenario_stress[scenario["name"]] = stress[0]
    require(set(scenario_stress.values()) == set(STRESSES), "all base/half_depth/plus_1cent scenarios required")
    metrics = read(root / "metrics.json")
    require(completion.get("candidate_count") == len(candidates)
            and completion.get("scenario_count") == len(scenarios)
            and completion.get("execution_scenario_rows") == len(metrics), "completion inventory/count mismatch")
    cells = {}
    for row in metrics:
        key = (row["candidate_id"], scenario_stress[row["scenario"]], row["latency_ms"])
        require(key not in cells and key[0] in candidates and key[2] in (500, 600, 1000), "duplicate/unexpected metric cell")
        require(row.get("initial_cash_usd") == 1000 and row.get("requested_shares") == 100
                and row.get("cash_release_delay_ms") == delay, "summary benchmark mismatch")
        cells[key] = row
    for scenario in scenarios:
        name = scenario["name"]
        audit = read(root / name / "audit.json")
        require(audit.get("mode") == "isolated" and audit.get("engine_config") == scenario["config"]
                and audit.get("registration") == registration and audit.get("constraints_mode") == "required"
                and audit.get("inherited_execution_assumptions") == [], "audit lacks native independent execution proof")
        require(audit["reporting"].get("reconciliation_differences") == [], "report accounting reconciliation failed")
        expected_rows = [{"scenario": name, **row} for row in audit["summary"]]
        require(expected_rows == [r for r in metrics if r["scenario"] == name], "metrics/audit summary mismatch")
        count = Counter()
        with gzip.open(root / name / "attempts.jsonl.gz", "rt") as stream:
            for line in stream:
                attempt = json.loads(line, object_pairs_hook=_object)
                require(finite(attempt.get("decision_ms")) and start <= attempt["decision_ms"] < end
                        and attempt["market_id"] in pinned, "attempt outside exact seven-day window")
                key = (attempt["candidate_id"], scenario_stress[name], attempt["latency_ms"])
                require(key in cells, "attempt without metric cell")
                count[key] += 1
                if (attempt.get("filled_shares") or 0) > 0:
                    require(attempt.get("settled") is True and all(isinstance(attempt.get(c), dict)
                            and attempt[c].get("validated") is True
                            for c in ("constraints_at_decision", "constraints_at_match")), "filled position lacks native constraints or settlement")
        for key, row in cells.items():
            if key[1] == scenario_stress[name]:
                require(row["attempts"] == count[key], "attempt/summary count mismatch")
    ranking, unranked = [], []
    for cid in candidates:
        rows, reasons = [], []
        for stress in STRESSES:
            for latency in (500, 600, 1000):
                row = cells.get((cid, stress, latency))
                rows.append({"stress": stress, "latency_ms": latency, "metrics": row})
                if row is None or row.get("status") != "simulated_complete" or not finite(row.get("net_pnl_usd")) or not finite(row.get("filled_shares")) or row["filled_shares"] <= 0 or row.get("unresolved_positions") != 0 or row.get("missing_execution_inputs") is not False:
                    reasons.append({"stress": stress, "latency_ms": latency, "status": None if row is None else row.get("status")})
        if reasons:
            unranked.append({"candidate_id": cid, "status": "pending_or_unranked", "reasons": reasons, "cells": rows})
        else:
            ranking.append({"candidate_id": cid, "primary_net_pnl_usd": cells[cid, "base", 500]["net_pnl_usd"], "cells": rows})
    ranking.sort(key=lambda r: (-r["primary_net_pnl_usd"], r["candidate_id"]))
    return {"status": "assessed_development_only", "ranking": [{"rank": i + 1, **r} for i, r in enumerate(ranking)],
            "unranked": unranked, "run_key": registration["run_key"], "coverage_days": 7, "market_slots": 2016}


def assess(protocol, run_root):
    result = {"schema": "rank7-assessment-v1", "protocol_sha256": fingerprint(protocol), "protocol": protocol,
              "formal_oos_result": None, "qualified_strategies": 0, "net_pnl_usd": None,
              "limitations": ["Development ranking only; seven days do not establish significant OOS edge.",
                              "Declared spans and market presence do not certify continuous feed health.",
                              "Pinned BTC universe and freeze timestamps need external provenance.",
                              "Displayed depth is a capacity upper bound, not observed private fills."]}
    try:
        result.update(_assess(protocol, Path(run_root).resolve()))
    except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
        result.update(status="blocked", reasons=[str(exc)], ranking=[], unranked=[])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = assess(read(args.protocol), args.run)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "protocol_sha256", "formal_oos_result", "qualified_strategies")}))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
