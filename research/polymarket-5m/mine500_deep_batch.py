"""Registered multi-archive DEVELOPMENT experiments, never a holdout evaluator.

Runs 32 path trials and eight fixed market-prior residual trials. The optional
720-rule reference is a transport diagnostic of existing rules, not new trials.
All intervals, missing outcomes, failures, and scenario alternatives are kept.
No networking, orders, production configuration, or parameter optimization.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path

import mine500_catalog as old
import mine500_path_candidates as path
import mine500_probability_models as probability
import mine500_replay_v2 as execution
from mine500_shared_portfolio import first_signal_per_market, simulate_shared_cash
from mine500_statistics import analyze

VERSION = "mine500-deep-development-v1"
CONSUMED_DAYS = ("2026-10-07", "2026-10-08", "2026-10-09")
SCENARIOS = (
    ("size5", {"shares": 5.}),
    ("size10", {"shares": 10.}),
    ("size20", {"shares": 20.}),
    ("capacity100", {"shares": 100.}),
    ("capacity500", {"shares": 500.}),
    ("half_depth", {"shares": 5., "depth_fraction": .5}),
    ("extra_1c", {"shares": 5., "extra_cost_per_filled_share_usd": .01}),
    ("assumed_min_dollar", {"shares": 5., "min_notional_usd": 1.}),
)


def digest(filename):
    h = hashlib.sha256()
    with Path(filename).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(filename, value):
    Path(filename).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                         allow_nan=False) + "\n")


def write_gzip(filename, rows):
    # Deterministic compressed bytes, independent of wall clock and output name.
    count = 0
    with Path(filename).open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f:
            for row in rows:
                f.write((json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode())
                count += 1
    return count


@dataclass(frozen=True)
class ReplayTape:
    """Reentrant on-disk event sequence; no market record cache is retained."""
    path: Path
    count: int

    def __iter__(self):
        return execution.read_jsonl(self.path)

    def __len__(self):
        return self.count


def _finite_clock(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validated_stream(iterator, start, end, initial_recv, filename):
    """Validate each source before heap merge; labels may arrive after capture."""
    previous = initial_recv
    for event in iterator:
        now = event.get("recv_ms")
        if not _finite_clock(now) or now < previous:
            raise ValueError(f"invalid or regressed receipt clock in {filename}")
        previous = now
        if event.get("type") in ("decision", "book") and not start <= now <= end:
            raise ValueError(f"book/decision outside own recording interval in {filename}")
        yield event


def combine_inputs(filenames, materialize_output=None):
    """Merge disjoint observed intervals, retaining label receipt times and gaps."""
    if not filenames:
        raise ValueError("at least one development input required")
    streams, headers, intervals, provenance = [], [], [], []
    for filename in filenames:
        it = iter(execution.read_jsonl(filename))
        header = next(it, {})
        if header.get("type") != "dataset" or header.get("split") != "development":
            raise ValueError("only consumed development tapes with headers are allowed")
        if header.get("book_resolution") != "decision_and_match_projection":
            raise ValueError("expected exact latency projection input")
        if set(header.get("supported_latencies_ms", [])) != {500, 600, 1000}:
            raise ValueError("unsupported matching projection")
        if header.get("decision_grid_ms") != 1000:
            raise ValueError("incompatible decision grid")
        start, end = header["coverage_start_ms"], header["coverage_end_ms"]
        if (not all(_finite_clock(x) for x in (start, end, header.get("recv_ms")))
                or not start < end or header["recv_ms"] > start):
            raise ValueError("invalid input interval")
        intervals.append((start, end))
        headers.append(header)
        streams.append(_validated_stream(it, start, end, header["recv_ms"], filename))
        provenance.append({"file": str(Path(filename).resolve()), "sha256": digest(filename),
                           "dataset_id": header["dataset_id"], "start_ms": start, "end_ms": end})
    for previous, current in zip(sorted(intervals), sorted(intervals)[1:]):
        if current[0] <= previous[1]:
            raise ValueError("overlapping recording intervals need explicit de-duplication")
    header = {"type": "dataset", "recv_ms": min(h["recv_ms"] for h in headers),
        "dataset_id": VERSION, "split": "development",
        "coverage_start_ms": min(x[0] for x in intervals),
        "coverage_end_ms": max(x[1] for x in intervals),
        "observed_hours": sum(b-a for a, b in intervals)/3600000,
        "observed_intervals_ms": sorted(intervals),
        "book_resolution": "decision_and_match_projection",
        "supported_latencies_ms": [500, 600, 1000], "decision_grid_ms": 1000,
        "provenance": {"source": "authorized_GitHub_Actions_consumed_development", "inputs": provenance},
        "gaps": "unobserved time is never interpolated or counted as observed hours",
        "full_tick_change_stream_preserved": all(h.get("full_tick_change_stream_preserved") is True for h in headers)}
    decisions, counts = set(), Counter()

    def validated_merge():
        yield header
        previous = header["recv_ms"]
        for event in heapq.merge(*streams, key=lambda e: e["recv_ms"]):
            if event["recv_ms"] < previous:
                raise ValueError("input stream regressed")
            previous = event["recv_ms"]
            if event["type"] == "dataset":
                raise ValueError("embedded dataset header")
            if event["type"] == "decision":
                day = datetime.fromtimestamp(event["recv_ms"]/1000, timezone.utc).date().isoformat()
                if day not in CONSUMED_DAYS:
                    raise ValueError("unconsumed date; old frozen windows must remain untouched")
                key = event["market_id"], event["recv_ms"]
                if key in decisions:
                    raise ValueError("duplicate decision across archives")
                decisions.add(key)
            counts[event["type"]] += 1
            yield event

    if materialize_output is None:
        result = list(validated_merge())
    else:
        destination = Path(materialize_output)
        if destination.resolve() in {Path(filename).resolve() for filename in filenames}:
            raise ValueError("materialized tape cannot overwrite an input")
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = ReplayTape(destination, write_gzip(destination, validated_merge()))
    return result, {"headers": headers, "input_hashes": {p["file"]:p["sha256"] for p in provenance},
                    "record_counts": dict(counts), "dataset": header}


def signal(candidate, features):
    if candidate["id"].startswith("PATH-"):
        return path.signal(candidate, features)
    if candidate["id"].startswith("MPR-"):
        return probability.signal(candidate, features)
    return old.signal(candidate, features)


def outcome_bounds(rows):
    settled = [r for r in rows if r.get("settled") and r["filled_shares"] > 0]
    opened = [r for r in rows if not r.get("settled") and r["filled_shares"] > 0]
    realized = math.fsum(r["net_pnl_usd"] for r in settled)
    return {"settled_pnl_usd": realized, "unresolved_filled_orders": len(opened),
        "unresolved_cost_usd": math.fsum(r["cost_usd"] for r in opened),
        "unresolved_shares": math.fsum(r["filled_shares"] for r in opened),
        "settlement_pnl_lower_bound_usd": realized - math.fsum(r["cost_usd"] for r in opened),
        "settlement_pnl_upper_bound_usd": realized + math.fsum(r["filled_shares"]-r["cost_usd"] for r in opened),
        "bounds_scope": "recorded simulated fills only; incomplete matching horizons are not bounded"}


def describe(result):
    groups, daily = defaultdict(list), defaultdict(list)
    for row in result["attempts"]:
        key = row["candidate_id"], row["latency_ms"]
        groups[key].append(row)
        day = datetime.fromtimestamp(row["decision_ms"]/1000, timezone.utc).date().isoformat()
        daily[(*key, day)].append(row)
    for row in result["summary"]:
        row.update(outcome_bounds(groups[row["candidate_id"], row["latency_ms"]]))
    return [{"candidate_id": cid, "latency_ms": latency, "day_utc": day,
             "attempts": len(rows), "submitted": sum(r["submitted"] for r in rows),
             "full_fills": sum(r["status"] == "filled_full" for r in rows),
             "partial_fills": sum(r["status"] == "filled_partial" for r in rows),
             "filled_shares": sum(r["filled_shares"] for r in rows),
             "fees_usd": sum(r["fees_usd"] for r in rows),
             "status_counts": dict(Counter(r["status"] for r in rows)), **outcome_bounds(rows)}
            for (cid, latency, day), rows in sorted(daily.items())]


def panel_results(doc):
    """Outcome-blind first signal among ALL 40 new rules, one position per market."""
    result = []
    for latency in doc["config"]["latencies_ms"]:
        ids = sorted({r["candidate_id"] for r in doc["attempts"] if r["latency_ms"] == latency})
        if not ids:
            result.append({"latency_ms": latency, "status": "no_signals", "net_pnl_usd": None})
            continue
        chosen, conflicts = first_signal_per_market(doc["attempts"], ids, latency)
        # A zero recorded fill at an unobserved horizon is missing execution,
        # not a proved no-fill. It must not become zero PnL in the cash combiner.
        missing_markers = ("missing_book", "missing_or_invalid_fee", "missing_asks",
            "invalid_asks", "fee_changed_after_decision", "invalid_min_order_size_metadata",
            "missing_required_constraints", "invalid_constraint_timestamp",
            "invalid_up_tick_size_metadata", "invalid_down_tick_size_metadata")
        incomplete = [r for r in chosen if str(r.get("status", "")).startswith(("incomplete_", "blocked_"))
                      or any(marker in r.get("status", "") for marker in missing_markers)]
        if incomplete:
            result.append({"latency_ms": latency, "status": "blocked_incomplete_panel_execution",
                           "net_pnl_usd": None, "chosen_attempts": chosen, "conflicts": conflicts,
                           "incomplete_chosen_attempts": len(incomplete)})
            continue
        # The existing shared-cash module requires all fills settled; never drop
        # an unresolved position to make a supposedly complete portfolio.
        if any(r["filled_shares"] > 0 and not r["settled"] for r in chosen):
            result.append({"latency_ms": latency, "status": "blocked_unresolved_panel_positions",
                           "net_pnl_usd": None, "chosen_attempts": chosen, "conflicts": conflicts})
            continue
        rows, summary = simulate_shared_cash(chosen, 1000.)
        result.append({"latency_ms": latency, "status": "simulated_shared_cash",
            "policy": "earliest_first_signal_all_40; same_time_tie_candidate_id; failed_first_signal_consumes_market",
            "selection_uses_pnl": False, **summary, "chosen_attempts": rows, "conflicts": conflicts})
    return result


def panel_intent_results(events, candidates, signal_fn, config):
    """Generate uncensored execution intents, then apply the single $1000 ledger.

    Candidate-specific $1000 rejections must not suppress an order that the
    shared panel could afford after choosing another candidate on prior markets.
    The large finite cash ceiling generates intents only, never portfolio capital.
    """
    intent_config = replace(config, initial_cash_usd=1e12)
    intent_doc = execution.replay_events(events, candidates, signal_fn, intent_config)
    if any(row["status"] == "rejected_insufficient_available_cash" for row in intent_doc["attempts"]):
        raise ValueError("finite intent cash ceiling bound; panel input remains capital censored")
    results = panel_results(intent_doc)
    for result in results:
        result["intent_cash_ceiling_usd"] = intent_config.initial_cash_usd
        result["portfolio_cash_usd"] = 1000.0
        result["intent_capital_censoring"] = False
    return results, intent_doc


def correct_current_coverage_denominator(stats, candidate_count, multiplicity_lower_bound):
    """Separate current coverage from historical multiple-testing denominator."""
    stats["current_registered_candidate_count"] = candidate_count
    stats["historical_multiplicity_lower_bound"] = multiplicity_lower_bound
    for audit in stats.get("denominator_audit", []):
        audit["declared_rules"] = candidate_count
        audit["missing_runner_summaries"] = max(0, candidate_count-audit["registered_rules_in_runner"])
        audit["historical_multiplicity_lower_bound"] = multiplicity_lower_bound
        audit["coverage_scope"] = "current_batch_candidates_only"
    return stats


def run(inputs, output, include_reference=True):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    events, inputs_audit = combine_inputs(inputs, materialize_output=output/"combined_input.jsonl.gz")
    candidates = path.make_candidates() + probability.make_candidates()
    modules = [Path(__file__), Path(path.__file__), Path(probability.__file__), Path(execution.__file__),
               Path(old.__file__), *[Path(__file__).with_name(name) for name in
                   ("mine500_constraints.py", "mine500_statistics.py", "mine500_shared_portfolio.py",
                    "mine500_constraint_extract.py")]]
    registration = {"schema": VERSION, "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates, "new_trials": 40, "existing_reference_trials": 720,
        "cumulative_mine500_trial_lower_bound": 760,
        "known_multiplicity_lower_bound_including_zoo100": 860,
        "other_historical_adaptive_trials": "additional; not erased or claimed fully enumerated",
        "scenarios": [{"name": n, "config": asdict(execution.ReplayConfig(**kw))} for n,kw in SCENARIOS],
        "input_hashes": inputs_audit["input_hashes"],
        "code_hashes": {p.name:digest(p) for p in modules},
        "consumed_decision_days": CONSUMED_DAYS, "formal_oos_results": None,
        "existing_holdout_protocols_modified": False,
        "shared_panel_policy": "earliest signal among all new40; no PnL selection; one attempt per market",
        "limitations": ["Recorded depth is a counterfactual capacity bound, not actual fills.",
            "Cost, depth and dollar-minimum scenarios are sensitivity assumptions, not observed rules.",
            "Stored book tick snapshots do not prove every intervening tick-change was retained.",
            "Three UTC dates do not justify a day-cluster significance claim."]}
    write_json(output/"registration.json", registration)
    write_json(output/"input_audit.json", inputs_audit)
    print("registered 40 new trials before fitting/replay", flush=True)
    fit_audit, scores = probability.train_forward_days(events, consumed_days=CONSUMED_DAYS,
                                                     input_hashes=inputs_audit["input_hashes"])
    write_json(output/"probability_folds.json", fit_audit)
    enriched_path = output/"enriched_development_events.jsonl.gz"
    enriched_count = write_gzip(enriched_path, probability.attach_scores(path.augment_events(events), scores))
    enriched = ReplayTape(enriched_path, enriched_count)
    del scores  # Joined values are now on the tape; do not retain the score-map cache.
    all_summary, all_daily, panels = [], [], []
    for name, parameters in SCENARIOS:
        print("replaying " + name, flush=True)
        doc = execution.replay_events(enriched, candidates, signal, execution.ReplayConfig(**parameters))
        daily = describe(doc)
        panel, intent_doc = panel_intent_results(enriched, candidates, signal, execution.ReplayConfig(**parameters))
        panels.append({"scenario": name, "results": panel})
        write_gzip(output/(name+"_panel_intent_attempts.jsonl.gz"), intent_doc.pop("attempts"))
        write_json(output/(name+"_panel_intent_replay.json"), intent_doc)
        stats = correct_current_coverage_denominator(analyze(doc, family_tests=860), len(candidates), 860)
        write_json(output/(name+"_statistics.json"), stats)
        write_gzip(output/(name+"_attempts.jsonl.gz"), doc.pop("attempts"))
        write_json(output/(name+"_replay.json"), doc)
        all_summary += [dict(r, scenario=name) for r in doc["summary"]]
        all_daily += [dict(r, scenario=name) for r in daily]
    write_json(output/"new_candidate_metrics.json", all_summary)
    write_json(output/"new_candidate_daily.json", all_daily)
    write_json(output/"shared_cash_panels.json", panels)
    if include_reference:
        print("replaying existing720 reference; not 720 new trials", flush=True)
        doc = execution.replay_events(events, old.make_candidates(), old.signal, execution.ReplayConfig())
        write_json(output/"reference720_daily.json", describe(doc))
        write_gzip(output/"reference720_attempts.jsonl.gz", doc.pop("attempts"))
        write_json(output/"reference720_replay.json", doc)
    code_changed = {p.name:digest(p) for p in modules} != registration["code_hashes"]
    input_changed = {filename:digest(filename) for filename in registration["input_hashes"]} != registration["input_hashes"]
    if code_changed or input_changed:
        write_json(output/"invalidated_run.json", {"reason": "code_or_input_changed_during_execution",
                                                   "code_changed": code_changed, "input_changed": input_changed,
                                                   "formal_oos_result": None})
        raise ValueError("registered code or input changed during execution; preserve outputs as invalidated")
    write_json(output/"completion.json", {"completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "new_candidate_scenarios": len(all_summary), "reference_candidate_scenarios": 2160 if include_reference else 0,
        "new_registered_trials": 40, "simulations_only": True, "formal_oos_result": None,
        "confirmed_strategies": 0, "observed_hours": inputs_audit["dataset"]["observed_hours"]})
    print("completed; no OOS qualification", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--skip-reference", action="store_true")
    args = parser.parse_args()
    run(args.inputs, args.out, not args.skip_reference)
