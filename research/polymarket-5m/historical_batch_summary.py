"""Summarize completed multi-day replay runs without selecting strategies.

Usage:
  python historical_batch_summary.py RUN_DIR [RUN_DIR ...] --out REPORT_DIR

Accepts completed replay_lab run directories, their metrics.json, or a scenario
metrics.csv. Different runs, scenarios, candidates and latency ledgers stay
separate. To obtain a continuous multi-day cash result, merge input recordings
and run the engine once; this module never adds independently reset accounts.
All confidence intervals are descriptive development diagnostics, not OOS tests.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import date as calendar_date, datetime, timedelta, timezone
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import random


SCHEMA = "historical-batch-diagnostics-v1"
COMPLETE_EXECUTION = {"simulated_complete", "evaluated_no_fills"}
COUNT_FIELDS = ("attempts", "submitted_orders", "filled_orders", "full_fills", "partial_fills",
                "failed_or_unfilled_attempts", "not_submitted_attempts", "submitted_but_unfilled_orders",
                "signal_markets", "filled_markets", "settled_positions", "unresolved_positions")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")


def decode(value):
    if value in ("", None):
        return None
    if value in ("True", "true"):
        return True
    if value in ("False", "false"):
        return False
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {key: decode(value) for key, value in row.items()}


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (dict, list, tuple)) else value
                             for key, value in row.items()})


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def key(row):
    return str(row["candidate_id"]), int(row["latency_ms"])


def complete_day(row):
    """A recording span is not healthy coverage. Require explicit presence too."""
    expected = row.get("expected_calendar_slots", row.get("expected_slots"))
    return (expected == 288 and (row.get("observed_seconds") or 0) >= 86400
            and row.get("complete_span_slots") == 288 and row.get("data_present_slots") == 288)


def observed_day(row):
    return any(finite(row.get(field)) and row[field] > 0 for field in
               ("observed_seconds", "recorded_span_slots", "decision_markets", "data_present_slots"))


def calendar_diagnostics(days):
    dates = {}
    for row in days:
        value = row.get("market_start_utc_date")
        if value and value != "unknown":
            dates[calendar_date.fromisoformat(value)] = row
    if not dates:
        return {"calendar_span_dates": None, "unreported_calendar_dates": [],
                "longest_consecutive_complete_days": 0, "all_dates_complete": False}
    cursor, end = min(dates), max(dates)
    count = longest = running = 0
    missing = []
    while cursor <= end:
        count += 1
        if cursor not in dates:
            missing.append(cursor.isoformat())
        running = running + 1 if cursor in dates and complete_day(dates[cursor]) else 0
        longest = max(longest, running)
        cursor += timedelta(days=1)
    return {"calendar_span_dates": count, "unreported_calendar_dates": missing,
            "longest_consecutive_complete_days": longest,
            "all_dates_complete": longest == count and len(dates) == len(days)}


def quantile(values, q):
    index = (len(values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def day_bootstrap(days, samples=2000, seed=0):
    """Resample UTC-market-date clusters, retaining each day's entire ledger.

    No multiplication by 288, extrapolation to full days, or use of missing
    calendar days as zero observations. Dates with actual coverage and observed
    no signals contribute zero to the settled subset, explicitly as such.
    """
    usable = [row for row in days if observed_day(row)
              and finite(row.get("settled_subset_pnl_usd"))
              and finite(row.get("settled_subset_shares"))]
    if len(usable) < 2:
        return {"status": "unavailable_fewer_than_two_observed_days", "day_clusters": len(usable),
                "mean_observed_day_pnl_95pct_interval_usd": None,
                "settled_share_pnl_95pct_interval_usd": None}
    rng = random.Random(seed)
    daily_means, share_ratios = [], []
    n = len(usable)
    for _ in range(samples):
        draw = [usable[rng.randrange(n)] for _ in range(n)]
        pnl = sum(row["settled_subset_pnl_usd"] for row in draw)
        shares = sum(row["settled_subset_shares"] for row in draw)
        daily_means.append(pnl / n)
        if shares > 0:
            share_ratios.append(pnl / shares)
    daily_means.sort()
    share_ratios.sort()
    return {"status": "descriptive_development_only_not_selection_adjusted", "day_clusters": n,
            "partial_calendar_days": sum(not complete_day(row) for row in usable),
            "samples": samples, "seed": seed,
            "mean_observed_day_pnl_95pct_interval_usd": [quantile(daily_means, .025), quantile(daily_means, .975)],
            "settled_share_pnl_95pct_interval_usd": [quantile(share_ratios, .025), quantile(share_ratios, .975)]
            if share_ratios else None,
            "zero_share_bootstrap_draws": samples - len(share_ratios),
            "interpretation": "Settled subset on recorded portions of UTC dates; no full-day extrapolation. "
                              "Serial dependence and prior multiple selection are not corrected; few-day intervals are unstable.",
            "qualified_edge": False}


def resolve_inputs(paths):
    """Deduplicate inputs and select scenario(s), never use incomplete stages."""
    selected = {}
    for supplied in paths:
        path = Path(supplied).resolve()
        root, scenario = path, None
        if path.name == "metrics.csv":
            root, scenario = path.parent.parent, path.parent.name
        elif path.name == "metrics.json":
            root = path.parent
        elif (path / "metrics.csv").exists() and not (path / "completion.json").exists():
            root, scenario = path.parent, path.name
        if any(".pending." in part for part in root.parts):
            raise ValueError(f"unfinished stage is not a completed run: {root}")
        completion = read_json(root / "completion.json")
        if completion.get("status") != "completed_development_simulation":
            raise ValueError(f"unsupported or unfinished run: {root}")
        choices = selected.setdefault(root, set())
        choices.add(scenario)
    return selected


def checked_file(root, relative, completion, hashes):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("report path escapes completed run")
    actual = digest(path)
    expected = completion.get("output_sha256", {}).get(relative)
    if not expected or actual != expected:
        raise ValueError(f"missing or mismatched completion hash: {path}")
    hashes[relative] = actual
    return path


def attempt_totals(path, daily_dates, eligible_keys, market_starts):
    totals = defaultdict(lambda: {"settled_subset_pnl_usd": 0., "settled_subset_shares": 0.,
                                  "settled_positions_checked": 0, "attempts_checked": 0})
    for k, dates in daily_dates.items():
        if k in eligible_keys:
            for date in dates:
                totals[(*k, date)]
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            k = key(row)
            start = row.get("market_start_ms")
            # The writer may omit market_start_ms in attempts; use registered
            # market metadata, never the decision/settlement date as a proxy.
            if not finite(start):
                start = market_starts.get(row["market_id"])
            date = datetime.fromtimestamp(start / 1000, timezone.utc).date().isoformat() if finite(start) else "unknown"
            total = totals[(*k, date)]
            total["attempts_checked"] += 1
            if row.get("settled") is True and finite(row.get("net_pnl_usd")):
                total["settled_subset_pnl_usd"] += row["net_pnl_usd"]
                total["settled_subset_shares"] += row.get("filled_shares", 0) or 0
                total["settled_positions_checked"] += 1
    return totals


def cash_diagnostics(path, common):
    states, daily = {}, {}
    for row in read_csv(path):
        if row.get("report_cash_basis") != "engine_recorded_event":
            continue
        k = key(row)
        state = states.setdefault(k, {"last_ms": -math.inf, "last_sequence": -math.inf,
                                     "realized": 0., "peak": 0., "drawdown": 0., "events": 0,
                                     "min_available_cash_usd": math.inf})
        ms, sequence = row.get("event_ms"), row.get("event_sequence")
        if not finite(ms) or not finite(sequence):
            raise ValueError("cash event requires engine timestamp and sequence")
        if ms < state["last_ms"] or sequence <= state["last_sequence"]:
            raise ValueError("cash ledger is not chronological in each candidate/latency account")
        date = datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()
        d = daily.setdefault((*k, date), {**common, "candidate_id": k[0], "latency_ms": k[1],
             "cash_event_utc_date": date, "events": 0, "opening_realized_pnl_usd": state["realized"],
             "closing_realized_pnl_usd": state["realized"], "_peak": state["realized"],
             "intraday_realized_drawdown_usd": 0., "min_available_cash_usd": math.inf,
             "drawdown_basis": "engine_realized_equity_events_not_mark_to_market"})
        pnl = row.get("realized_pnl_usd")
        available = (row.get("cash_usd") or 0) - (row.get("reserved_usd") or 0)
        if not finite(pnl):
            raise ValueError("cash event lacks finite cumulative realized PnL")
        state.update(last_ms=ms, last_sequence=sequence, realized=pnl)
        state["peak"] = max(state["peak"], pnl)
        state["drawdown"] = max(state["drawdown"], state["peak"] - pnl)
        state["min_available_cash_usd"] = min(state["min_available_cash_usd"], available)
        state["events"] += 1
        d["_peak"] = max(d["_peak"], pnl)
        d["intraday_realized_drawdown_usd"] = max(d["intraday_realized_drawdown_usd"], d["_peak"] - pnl)
        d["closing_realized_pnl_usd"] = pnl
        d["min_available_cash_usd"] = min(d["min_available_cash_usd"], available)
        d["events"] += 1
    result = []
    for row in daily.values():
        row.pop("_peak")
        row["realized_pnl_change_usd"] = row["closing_realized_pnl_usd"] - row["opening_realized_pnl_usd"]
        result.append(row)
    return states, result


def overlap_report(runs):
    comparisons = []
    for left, right in itertools.combinations(runs, 2):
        if left["run_key"] == right["run_key"]:
            continue
        common = set(left["market_ids"]) & set(right["market_ids"])
        comparisons.append({"left_run": left["run_key"], "right_run": right["run_key"],
                            "shared_registered_markets": len(common),
                            "same_event_tape": left["events_sha256"] == right["events_sha256"],
                            "aggregate_pnl_allowed": False,
                            "reason": "Runs retain independent engine/cash identities; replay the merged tape for continuous results."})
    return comparisons


def summarize(paths, out, bootstrap_samples=2000):
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    selected = resolve_inputs(paths)
    summaries, all_daily, cash_daily, provenance, overlaps, mismatches = [], [], [], [], [], []
    for root, requested_scenarios in sorted(selected.items()):
        completion, hashes = read_json(root / "completion.json"), {}
        registration = read_json(checked_file(root, "registration.json", completion, hashes))
        coverage = read_json(checked_file(root, "coverage.json", completion, hashes))
        registered = {str(row["market_id"]): row for row in coverage.get("markets", registration.get("markets", []))}
        metadata_dates = {mid: row.get("start_ms") for mid, row in registered.items()}
        scenario_names = [row["name"] for row in registration["scenarios"]]
        if None not in requested_scenarios:
            if not requested_scenarios <= set(scenario_names):
                raise ValueError("requested scenario absent from registration")
            scenario_names = [name for name in scenario_names if name in requested_scenarios]
        run_key = completion["run_key"]
        run_provenance = {"run_key": run_key, "source_run": str(root),
                          "completion_sha256": digest(root / "completion.json"),
                          "events_sha256": registration.get("events_sha256"),
                          "cache_key": registration.get("cache_key"), "input_sha256": hashes,
                          "scenarios": scenario_names, "registered_markets": len(registered),
                          "input_data_role": registration.get("input_data_role"),
                          "engine_code_sha256": registration.get("code_sha256"),
                          "registered_candidates": len(registration.get("strategies", {}).get("candidate_rules", []))}
        provenance.append(run_provenance)
        overlaps.append({"run_key": run_key, "market_ids": list(registered),
                         "events_sha256": registration.get("events_sha256")})
        for scenario in scenario_names:
            common = {"run_key": run_key, "scenario": scenario, "mode": registration.get("mode")}
            paths_checked = {name: checked_file(root, f"{scenario}/{name}", completion, hashes)
                             for name in ("metrics.csv", "daily.csv", "cash_ledger.csv", "attempts.jsonl.gz", "audit.json")}
            metrics = list(read_csv(paths_checked["metrics.csv"]))
            if len({key(row) for row in metrics}) != len(metrics):
                raise ValueError("duplicate candidate/latency rows")
            grouped_days = defaultdict(list)
            for row in read_csv(paths_checked["daily.csv"]):
                grouped_days[key(row)].append(row)
            audit = read_json(paths_checked["audit.json"])
            if audit.get("reporting", {}).get("reconciliation_differences"):
                raise ValueError("source report has unresolved reconciliation differences")
            day_dates = {k: [row["market_start_utc_date"] for row in days] for k, days in grouped_days.items()}
            totals = attempt_totals(paths_checked["attempts.jsonl.gz"], day_dates,
                                    {key(row) for row in metrics if not str(row.get("status", "")).startswith("blocked_")},
                                    metadata_dates)
            totals_by_key = defaultdict(list)
            for (candidate, delay, date), total in totals.items():
                totals_by_key[candidate, delay].append(total)
            cash, cash_rows = cash_diagnostics(paths_checked["cash_ledger.csv"], common)
            cash_daily.extend(cash_rows)
            for metric in metrics:
                k = key(metric)
                days = grouped_days[k]
                if len({row["market_start_utc_date"] for row in days}) != len(days):
                    raise ValueError("duplicate daily rows")
                blocked = str(metric.get("status", "")).startswith("blocked_")
                enriched = []
                for day in sorted(days, key=lambda row: row["market_start_utc_date"]):
                    date = day["market_start_utc_date"]
                    total = totals.get((*k, date), {})
                    row = {**common, **day, **total,
                           "recording_day_complete": complete_day(day),
                           "recorded_scope_net_pnl_usd": day.get("net_pnl_usd")}
                    row["net_pnl_usd"] = day.get("net_pnl_usd") if complete_day(day) else None
                    if blocked or not observed_day(day):
                        row["settled_subset_pnl_usd"] = None
                        row["settled_subset_shares"] = None
                    row["settled_subset_is_incomplete"] = not (complete_day(day) and day.get("status") in COMPLETE_EXECUTION)
                    enriched.append(row)
                    all_daily.append(row)
                known_total = totals_by_key[k]
                attempt_count = sum(row["attempts_checked"] for row in known_total)
                settled_pnl = sum(row["settled_subset_pnl_usd"] for row in known_total)
                settled_shares = sum(row["settled_subset_shares"] for row in known_total)
                if attempt_count != metric.get("attempts", 0):
                    mismatches.append({**common, "candidate_id": k[0], "latency_ms": k[1], "field": "attempt_count",
                                       "source": metric.get("attempts"), "checked": attempt_count})
                if finite(metric.get("realized_pnl_usd")) and abs(metric["realized_pnl_usd"] - settled_pnl) > 1e-7:
                    mismatches.append({**common, "candidate_id": k[0], "latency_ms": k[1], "field": "settled_pnl",
                                       "source": metric["realized_pnl_usd"], "checked": settled_pnl})
                complete_execution = metric.get("status") in COMPLETE_EXECUTION and finite(metric.get("net_pnl_usd"))
                calendar = calendar_diagnostics(days)
                full_window = calendar["all_dates_complete"]
                state = cash.get(k)
                if state and finite(metric.get("max_realized_drawdown_usd")) and abs(state["drawdown"] - metric["max_realized_drawdown_usd"]) > 1e-7:
                    mismatches.append({**common, "candidate_id": k[0], "latency_ms": k[1], "field": "realized_drawdown",
                                       "source": metric["max_realized_drawdown_usd"], "checked": state["drawdown"]})
                result = {**common, "candidate_id": k[0], "latency_ms": k[1], "engine_status": metric.get("status"),
                          "registered_markets": len(registered), "calendar_dates": len(days),
                          "calendar_span_dates": calendar["calendar_span_dates"],
                          "unreported_calendar_dates": calendar["unreported_calendar_dates"],
                          "longest_consecutive_complete_days": calendar["longest_consecutive_complete_days"],
                          "observed_dates": sum(observed_day(day) for day in days),
                          "complete_recording_dates": sum(complete_day(day) for day in days),
                          "recorded_hours": sum(day.get("observed_seconds") or 0 for day in days) / 3600,
                          "expected_calendar_slots": calendar["calendar_span_dates"] * 288 if calendar["calendar_span_dates"] else None,
                          "recording_window_complete": full_window, "execution_complete_on_recorded_scope": complete_execution,
                          **{field: metric.get(field) for field in COUNT_FIELDS},
                          "filled_shares": metric.get("filled_shares"), "fees_usd": metric.get("fees_usd"),
                          "extra_cost_usd": metric.get("extra_cost_usd"),
                          "net_pnl_usd": metric.get("net_pnl_usd") if full_window and complete_execution else None,
                          "recorded_scope_net_pnl_usd": metric.get("net_pnl_usd"),
                          "recorded_scope_pnl_per_filled_share_usd": metric.get("net_pnl_per_filled_share_usd"),
                          "settled_subset_pnl_usd": None if blocked else settled_pnl,
                          "settled_subset_shares": None if blocked else settled_shares,
                          "settled_subset_pnl_per_share_usd": settled_pnl / settled_shares if not blocked and settled_shares > 0 else None,
                          "settled_subset_is_incomplete": not (full_window and complete_execution),
                          "unknown_market_date_attempts": totals.get((*k, "unknown"), {}).get("attempts_checked", 0),
                          "max_realized_drawdown_usd": state["drawdown"] if state else metric.get("max_realized_drawdown_usd"),
                          "drawdown_basis": "realized_equity_only_not_mark_to_market",
                          "cash_events_checked": state["events"] if state else 0,
                          "min_available_cash_usd": state["min_available_cash_usd"] if state else metric.get("min_available_cash_usd"),
                          "initial_cash_usd": metric.get("initial_cash_usd"), "ending_cash_usd": metric.get("ending_cash_usd"),
                          "capital_scope": metric.get("capital_scope"), "status_counts": metric.get("status_counts"),
                          "constraints_mode": audit.get("constraints_mode"),
                          "constraints_validated_attempts": metric.get("constraints_validated_attempts"),
                          "filled_positions_with_validated_constraints": metric.get("filled_positions_with_validated_constraints"),
                          "execution_assumptions": audit.get("inherited_execution_assumptions"),
                          "source_limitations": audit.get("limitations", []),
                          "formal_oos_result": None, "qualified_edge": False}
                seed = int(hashlib.sha256(f"{run_key}/{scenario}/{k}".encode()).hexdigest()[:16], 16)
                result["day_cluster_uncertainty"] = day_bootstrap(enriched, bootstrap_samples, seed)
                summaries.append(result)
    if mismatches:
        raise ValueError("diagnostic reconciliation failed: " + json.dumps(mismatches[:10]))
    output = Path(out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": SCHEMA, "source_run_count": len(provenance), "result_rows": len(summaries),
              "purpose": "Multi-day development diagnostics; no new replay or strategy selection",
              "policy": "Never add different runs, scenarios, candidates or latencies; portfolio results require one engine cash ledger.",
              "net_pnl_definition": "net_pnl_usd requires all represented UTC dates fully recorded and execution complete; "
                                    "recorded_scope_net_pnl_usd retains the engine value on observed data; "
                                    "settled_subset_pnl_usd excludes unresolved filled positions and is explicitly incomplete when applicable.",
              "capacity_basis": "Displayed depth simulation upper bound, not actual fills",
              "new_backtests": 0, "new_strategy_selections": 0, "formal_oos_result": None, "qualified_strategies": 0,
              "provenance": provenance, "overlap_comparisons": overlap_report(overlaps),
              "reconciliation_differences": [], "results": summaries}
    dump(output / "summary.json", report)
    write_csv(output / "summary.csv", summaries)
    write_csv(output / "daily.csv", all_daily)
    write_csv(output / "cash_daily.csv", cash_daily)
    lines = ["# 多日回放诊断", "", "这是已完成历史开发回放的汇总，没有运行新回测或筛选新策略。",
             "不同输入运行、策略、延迟、压力情景分别报告；共同资金组合必须以同一引擎账户回放，不能相加。",
             "每个 UTC 日理论 288 场。录制跨度覆盖不等于盘口健康；净收益主列仅在所列全部日期完整且执行完整时填写。",
             "录制区间收益保留引擎原值；已结算子集收益不能代替完整窗口收益。回撤仅按已实现权益，不是逐时盯市。",
             "日簇重采样只描述这批开发样本，未校正历史多重筛选，不能证明样本外优势。", "",
             "| 运行 | 情景 | 候选 | 延迟 ms | 日期/完整日 | 小时 | 注册市场 | 尝试 | 全成/部分/未成 | 完整窗口 PnL | 录制区间 PnL | 已结算子集 PnL |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def fmt(value):
        return "null" if value is None else f"{value:.6f}" if isinstance(value, float) else str(value)
    for row in summaries:
        lines.append("| " + " | ".join([row["run_key"][:12], row["scenario"], row["candidate_id"], str(row["latency_ms"]),
                    f'{row["calendar_dates"]}/{row["complete_recording_dates"]}', f'{row["recorded_hours"]:.3f}',
                    str(row["registered_markets"]), str(row["attempts"]),
                    f'{row["full_fills"]}/{row["partial_fills"]}/{row["failed_or_unfilled_attempts"]}',
                    fmt(row["net_pnl_usd"]), fmt(row["recorded_scope_net_pnl_usd"]), fmt(row["settled_subset_pnl_usd"])]) + " |")
    lines += ["", "逐日覆盖、尝试、失败和收益：daily.csv。现金事件日回撤：cash_daily.csv（日期口径与市场日不同）。",
              "来源哈希、限制、执行假设、日簇区间和跨运行重叠：summary.json。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return {"source_runs": len(provenance), "result_rows": len(summaries), "output": str(output),
            "new_backtests": 0, "new_strategy_selections": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()
    print(json.dumps(summarize(args.runs, args.out, args.bootstrap_samples), ensure_ascii=False))


if __name__ == "__main__":
    main()
