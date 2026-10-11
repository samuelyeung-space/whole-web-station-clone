"""Independent arithmetic/ledger audit of historical100 result artifacts.

Never imports the execution engine, fits a model, selects a strategy, or labels
this already examined B/C sample new OOS. Missing results are an error, not zero.
Run: python historical100_summary.py --input RESULT_DIR --out NEW_AUDIT_DIR
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while part := f.read(1 << 20):
            h.update(part)
    return h.hexdigest()


def utc_day(ms):
    return datetime.fromtimestamp(ms / 1000., timezone.utc).date().isoformat()


def segment(day):
    return "B" if "2026-07-16" <= day < "2026-08-16" else "C" if "2026-08-16" <= day < "2026-08-30" else "outside"


def fees(levels, rate):
    return math.fsum(float(p) * float(q) * rate * (1. - float(p)) for p, q in levels)


def audit_scenario(attempts, exits, events, reported, config, tolerance=1e-6):
    """Reconstruct every balance, reservation and allocated entry cost independently."""
    errors = []
    checks = 0
    def check(name, got, expected, where):
        nonlocal checks
        checks += 1
        if got is None or expected is None:
            okay = got is expected
        elif isinstance(expected, (int, float)):
            okay = isinstance(got, (int, float)) and math.isfinite(got) and abs(got - expected) <= tolerance
        else:
            okay = got == expected
        if not okay:
            errors.append({"where": where, "field": name, "recorded": got, "independent": expected})
    by_id = {}
    for a in attempts:
        aid = a["attempt_id"]
        if aid in by_id:
            errors.append({"where": aid, "field": "attempt_id", "error": "duplicate"})
        by_id[aid] = a
        levels = a.get("entry_levels", [])
        qty = math.fsum(q for p, q in levels)
        notional = math.fsum(p * q for p, q in levels)
        charge = fees(levels, a["fee_rate"])
        for key, expected in (("filled_shares", qty), ("entry_notional_usd", notional),
                              ("entry_fee_usd", charge), ("cost_usd", notional + charge)):
            check(key, a.get(key), expected, aid)
        if levels:
            if any(p > a["limit_price"] + tolerance or q < 0 for p, q in levels):
                errors.append({"where": aid, "error": "entry exceeds fixed limit or negative quantity"})
            if qty > a["requested_shares"] + tolerance:
                errors.append({"where": aid, "error": "entry exceeds requested quantity"})
        check("sold_plus_settled_not_over_entry", max(0., a["sold_shares"] + a["settlement_shares"] - qty), 0., aid)
    xs, x_totals = {}, defaultdict(lambda: defaultdict(float))
    for i, x in enumerate(exits):
        aid = x["attempt_id"]
        key = (aid, x.get("match_ms"))
        if key in xs:
            errors.append({"where": aid, "error": "duplicate exit match"})
        xs[key] = x
        levels = x.get("levels", [])
        q = math.fsum(qty for p, qty in levels)
        gross = math.fsum(p * qty for p, qty in levels)
        charge = fees(levels, by_id[aid]["fee_rate"])
        for name, expected in (("filled_shares", q), ("gross_usd", gross), ("fee_usd", charge)):
            check(name, x.get(name), expected, "exit:" + str(i))
        if levels and any(p + tolerance < x["limit_price"] or qty < 0 for p, qty in levels):
            errors.append({"where": aid, "error": "exit exceeds fixed sell limit or negative quantity"})
        x_totals[aid]["sold_shares"] += q
        x_totals[aid]["exit_gross_usd"] += gross
        x_totals[aid]["exit_fee_usd"] += charge
    for aid, a in by_id.items():
        for key in ("sold_shares", "exit_gross_usd", "exit_fee_usd"):
            check(key, a.get(key), x_totals[aid][key], aid)

    initial = float(config["initial_cash_usd"])
    cash, reserved, realized = initial, 0., 0.
    pending, positions = {}, {}
    min_available, max_reserved, max_capital, max_positions = initial, 0., 0., 0
    peak_realized_equity, dd = initial, 0.
    last_t = -math.inf
    seen_entry, seen_exit, seen_settlement = set(), set(), set()
    for i, e in enumerate(events):
        t, event, aid = e["time_ms"], e["event"], e.get("attempt_id")
        a = by_id.get(aid)
        where = "cash_event:" + str(i)
        if t < last_t:
            errors.append({"where": where, "error": "nonchronological events"})
        last_t = t
        if event == "pending_match":
            reserve = a["requested_shares"] * a["limit_price"] + fees([[a["limit_price"], a["requested_shares"]]], a["fee_rate"])
            check("reservation_usd", a["reservation_usd"], reserve, aid)
            if reserve > cash - reserved + tolerance:
                errors.append({"where": where, "error": "accepted unaffordable full request"})
            pending[aid] = reserve
            reserved += reserve
        elif event == "official_outcome_assumed_cash_release":
            if aid not in positions:
                errors.append({"where": where, "error": "settlement without open position"})
            else:
                qty, cost = positions.pop(aid)
                check("settlement_shares", a["settlement_shares"], qty, aid)
                payout = a["payout_usd"]
                if min(abs(payout), abs(payout - qty)) > tolerance:
                    errors.append({"where": aid, "error": "binary payout differs from zero/full remaining shares"})
                cash += payout
                realized += payout - cost
                seen_settlement.add(aid)
        elif event.startswith("exit_"):
            x = xs.get((aid, t))
            if x is None:
                errors.append({"where": where, "error": "cash exit has no exact matching exit row"})
            elif aid not in positions:
                errors.append({"where": where, "error": "exit without open position"})
            else:
                qty, cost = positions[aid]
                sold = x["filled_shares"]
                allocated = cost * sold / qty
                proceeds = x["gross_usd"] - x["fee_usd"]
                cash += proceeds
                realized += proceeds - allocated
                if qty - sold <= 1e-8:
                    del positions[aid]
                else:
                    positions[aid] = (qty - sold, cost - allocated)
                seen_exit.add((aid, t))
        elif aid in pending and t == a["match_ms"]:
            reserved -= pending.pop(aid)
            cash -= a["cost_usd"]
            if a["filled_shares"] > 1e-8:
                positions[aid] = (a["filled_shares"], a["cost_usd"])
            seen_entry.add(aid)
        available = cash - reserved
        open_cost = math.fsum(cost for qty, cost in positions.values())
        for key, expected in (("cash_usd", cash), ("reserved_usd", reserved),
                              ("available_cash_usd", available), ("open_cost_usd", open_cost),
                              ("realized_pnl_usd", realized), ("open_positions", len(positions))):
            check(key, e.get(key), expected, where)
        check("cash_plus_open_cost_minus_initial", cash + open_cost - initial, realized, where)
        if available < -tolerance or reserved < -tolerance:
            errors.append({"where": where, "error": "negative available cash or reservation"})
        min_available = min(min_available, available)
        max_reserved = max(max_reserved, reserved)
        max_capital = max(max_capital, open_cost + reserved)
        max_positions = max(max_positions, len(positions))
        peak_realized_equity = max(peak_realized_equity, initial + realized)
        dd = max(dd, peak_realized_equity - initial - realized)
    if pending:
        errors.append({"where": "end", "error": "unreleased pending reservations"})
    for aid, a in by_id.items():
        if a["filled_shares"] > 1e-8 and aid not in seen_entry:
            errors.append({"where": aid, "error": "filled entry absent from cash tape"})
        exitproceeds = a["exit_gross_usd"] - a["exit_fee_usd"]
        remaining_cost = positions.get(aid, (0., 0.))[1]
        check("pnl_usd", a["pnl_usd"], exitproceeds + a["payout_usd"] - a["cost_usd"] + remaining_cost, aid)
        if a["payout_usd"] and aid not in seen_settlement:
            errors.append({"where": aid, "error": "payout absent from cash tape"})
    fields = {"signal_attempts": len(attempts), "markets": len({a["market_id"] for a in attempts}),
        "filled_orders": sum(a["filled_shares"] > 1e-8 for a in attempts),
        "filled_shares": math.fsum(a["filled_shares"] for a in attempts),
        "entry_fee_usd": math.fsum(a["entry_fee_usd"] for a in attempts),
        "exit_fee_usd": math.fsum(x["fee_usd"] for x in exits),
        "ending_cash_usd": cash, "realized_pnl_usd": realized,
        "net_pnl_usd": None if positions else realized,
        "remaining_cost_usd": math.fsum(cost for qty, cost in positions.values()),
        "unresolved_positions": len(positions), "min_available_cash_usd": min_available,
        "max_reserved_cash_usd": max_reserved, "max_concurrent_positions": max_positions,
        "max_capital_at_cost_including_reserved_usd": max_capital,
        "max_realized_only_drawdown_usd": dd}
    for key, expected in fields.items():
        check(key, reported.get(key), expected, "summary")
    return {"checks": checks, "discrepancy_count": len(errors), "discrepancies": errors,
            "independent_summary": fields,
            "scope": "arithmetic/reservation/inventory/fee consistency only; not raw-book or authenticity verification"}


def aggregate(rows):
    filled = [a for a in rows if a["filled_shares"] > 1e-8]
    unresolved = sum(not a.get("fully_realized", False) for a in filled)
    realized = math.fsum(a["pnl_usd"] for a in rows)
    shares = math.fsum(a["filled_shares"] for a in rows)
    return {"attempts": len(rows), "fills": len(filled), "markets": len({a["market_id"] for a in rows}),
        "days": len({utc_day(a["decision_ms"]) for a in rows}),
        "filled_shares": shares, "realized_pnl_usd": realized,
        "net_pnl_usd": None if unresolved else realized,
        "net_per_filled_share_usd": realized / shares if shares and not unresolved else None,
        "entry_fee_usd": math.fsum(a["entry_fee_usd"] for a in rows),
        "exit_fee_usd": math.fsum(a["exit_fee_usd"] for a in rows),
        "cash_rejections": sum(a["status"] == "rejected_insufficient_cash" for a in rows),
        "full_fills": sum(a["status"] == "full_fill" for a in rows),
        "partial_fills": sum(a["status"] == "partial_fill" for a in rows),
        "unresolved_positions": unresolved}


def cluster_uncertainty(rows, key, replicates=2000, seed=20261011):
    """Whole-cluster exploratory bootstrap; NOT a corrected profitability verdict."""
    import numpy as np
    groups = defaultdict(list)
    for a in rows:
        groups[key(a)].append(a)
    totals = [aggregate(groups[k]) for k in sorted(groups)]
    n = len(totals)
    base = {"clusters": n, "bootstrap_replicates": replicates, "seed": seed,
            "multiple_testing_adjusted": False, "formal_significance": None,
            "selection_trials_lower_bound": 901,
            "warning": "previously examined sample; correlated rules and latency alternatives; exploratory uncertainty only"}
    if n < 2 or any(x["net_pnl_usd"] is None for x in totals):
        return {**base, "status": "insufficient_clusters_or_unresolved", "ci95_total_pnl_usd": None,
                "ci95_net_per_filled_share_usd": None}
    vals = np.array([[x["net_pnl_usd"], x["filled_shares"]] for x in totals], dtype=float)
    rng = np.random.default_rng(seed)
    sample_pnl, sample_edge = [], []
    for offset in range(0, replicates, 128):
        ids = rng.integers(0, n, size=(min(128, replicates - offset), n))
        sampled = vals[ids].sum(axis=1)
        sample_pnl.extend(sampled[:, 0].tolist())
        sample_edge.extend((sampled[sampled[:, 1] > 0, 0] / sampled[sampled[:, 1] > 0, 1]).tolist())
    return {**base, "status": "exploratory_only", "ci95_total_pnl_usd": np.quantile(sample_pnl, [.025, .975]).tolist(),
            "ci95_net_per_filled_share_usd": np.quantile(sample_edge, [.025, .975]).tolist() if sample_edge else None,
            "zero_filled_share_bootstrap_draws": replicates - len(sample_edge),
            "few_days_caution": n < 20,
            "resampling_assumption": "clusters independent; serial dependence across days not modelled; daily bootstrap preferred over market bootstrap"}


def overlap(rows):
    by_market = defaultdict(list)
    for a in rows:
        by_market[a["market_id"]].append(a)
    filled = [a for a in rows if a["filled_shares"] > 1e-8]
    return {"markets_with_multiple_signals": sum(len(g) > 1 for g in by_market.values()),
        "max_signals_one_market": max((len(g) for g in by_market.values()), default=0),
        "markets_signalling_both_sides": sum(len({a["side"] for a in g}) > 1 for g in by_market.values()),
        "markets_filled_both_sides": sum(len({a["side"] for a in g if a["filled_shares"] > 1e-8}) > 1 for g in by_market.values()),
        "same_clock_signal_multiplicity_max": max(Counter((a["market_id"], a["decision_ms"]) for a in rows).values(), default=0),
        "filled_orders": len(filled), "independent_mechanisms_claimed": 0,
        "interpretation": "MIX rules share inputs/models/markets; concurrent-position maximum is independently checked from cash tape"}


def run(input_dir, output_dir, replicates=2000):
    root, out = Path(input_dir), Path(output_dir)
    metrics_path = root / "metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError("actual metrics absent; no zero-result placeholder emitted: " + str(metrics_path))
    metrics = json.loads(metrics_path.read_text())
    if not metrics:
        raise ValueError("metrics contain no actual scenarios")
    if out.exists() and any(out.iterdir()):
        raise ValueError("audit output must be empty")
    out.mkdir(parents=True, exist_ok=True)
    report = {"format": "historical100-independent-summary-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "historical_capacity_revalidation_NOT_new_untouched_OOS",
        "script_sha256": digest(__file__), "inputs_sha256": {metrics_path.name: digest(metrics_path)},
        "attribution": "PnL and fees attributed to original signal UTC day, including later exits/settlement",
        "formal_new_oos_result": None, "qualified_new_mechanisms": 0, "scenarios": []}
    all_rows, grouped_csv = {}, []
    for metric in metrics:
        rule = metric.get("rule", metric.get("rule_id"))
        latency = metric["latency_ms"]
        stem = f"{rule}_{latency}ms"
        paths = {kind: root / (stem + "_" + kind + ".jsonl.gz") for kind in ("attempts", "exits", "cash_events")}
        paths["summary"] = root / (stem + "_summary.json")
        for path in paths.values():
            if not path.exists():
                raise FileNotFoundError("incomplete actual scenario: " + str(path))
            report["inputs_sha256"][path.name] = digest(path)
        rows, exits, events = (read_rows(paths[k]) for k in ("attempts", "exits", "cash_events"))
        declared = json.loads(paths["summary"].read_text())
        audit = audit_scenario(rows, exits, events, declared["summary"], declared["config"])
        for key, value in declared["summary"].items():
            if key in metric and metric[key] != value:
                audit["discrepancies"].append({"field": key, "error": "metrics versus scenario summary mismatch"})
                audit["discrepancy_count"] += 1
        scenario = {"rule": rule, "latency_ms": latency, "audit": audit, "all": aggregate(rows),
            "market_cluster_uncertainty": cluster_uncertainty(rows, lambda a: a["market_id"], replicates),
            "day_cluster_uncertainty": cluster_uncertainty(rows, lambda a: utc_day(a["decision_ms"]), replicates),
            "overlap": overlap(rows), "by_day": [], "by_segment": []}
        for label, func in (("day", lambda a: utc_day(a["decision_ms"])),
                            ("segment", lambda a: segment(utc_day(a["decision_ms"])))):
            groups = defaultdict(list)
            for a in rows:
                groups[func(a)].append(a)
            for key in sorted(groups):
                entry = {label: key, **aggregate(groups[key])}
                if label == "segment":
                    entry["market_cluster_uncertainty"] = cluster_uncertainty(groups[key], lambda a: a["market_id"], replicates)
                    entry["day_cluster_uncertainty"] = cluster_uncertainty(groups[key], lambda a: utc_day(a["decision_ms"]), replicates)
                scenario["by_" + label].append(entry)
                grouped_csv.append({"rule": rule, "latency_ms": latency, "group_type": label,
                                    "group": key, **aggregate(groups[key])})
        report["scenarios"].append(scenario)
        all_rows[(rule, latency)] = rows
    cross_rule = []
    for latency in sorted({l for r, l in all_rows}):
        for a, b in itertools.combinations(sorted({r for r, l in all_rows if l == latency}), 2):
            def keys(rule, filled=False):
                return {(x["market_id"], x["decision_ms"], x["side"]) for x in all_rows[(rule, latency)]
                        if not filled or x["filled_shares"] > 1e-8}
            left, right = keys(a), keys(b)
            lf, rf = keys(a, True), keys(b, True)
            union = left | right
            cross_rule.append({"latency_ms": latency, "rules": [a, b],
                "same_selected_signal_count": len(left & right), "signal_jaccard": len(left & right) / len(union) if union else None,
                "same_filled_signal_count": len(lf & rf), "independent_mechanisms": False})
    report["cross_rule_overlap"] = cross_rule
    report["audit_discrepancies_total"] = sum(x["audit"]["discrepancy_count"] for x in report["scenarios"])
    (out / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    if grouped_csv:
        with (out / "date_segment_metrics.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, list(grouped_csv[0]))
            writer.writeheader()
            writer.writerows(grouped_csv)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--replicates", type=int, default=2000)
    args = p.parse_args()
    if args.replicates < 100:
        raise ValueError("at least 100 exploratory bootstrap replicates")
    result = run(args.input, args.out, args.replicates)
    print(json.dumps({"scenarios": len(result["scenarios"]),
        "audit_discrepancies_total": result["audit_discrepancies_total"],
        "output": str(args.out / "summary.json")}))
    raise SystemExit(0 if result["audit_discrepancies_total"] == 0 else 2)


if __name__ == "__main__":
    main()
