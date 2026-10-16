"""Read completed MAR runs and compare every registered factor cell. No replay."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics


FACTORS = ("mechanism", "window", "reference_slope_gate", "smooth_only", "band", "edge")
DOMAINS = (("trend_pullback", "flat_overshoot", "btc_jump_poly_lag"), (15, 60, 120),
           (False, True), (False, True), (0.5, 1.0), (0.04, 0.08))
SCENARIOS = ("shares5_base_release0", "shares10_base_release0", "shares20_base_release0",
             "shares5_half_depth_release0", "shares5_plus_1cent_release0")
LATENCIES = (500, 600, 1000)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def csv_write(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                        for k, v in r.items()})


def key(row):
    return row["candidate_id"], row["scenario"], row["latency_ms"]


def read_report(directory):
    directory = Path(directory)
    path = directory / "summary.json"
    summary = json.loads(path.read_text())
    if summary.get("reconciliation_differences") or len(summary["provenance"]) != 1:
        raise ValueError("unreconciled or multi-run summary")
    p = summary["provenance"][0]
    run = Path(p["source_run"])
    completion = json.loads((run / "completion.json").read_text())
    if completion["status"] != "completed_development_simulation" or sha(run / "completion.json") != p["completion_sha256"]:
        raise ValueError("incomplete or changed source run")
    for filename in ("registration.json", "metrics.json", "coverage.json"):
        if sha(run / filename) != completion["output_sha256"][filename]:
            raise ValueError("source hash mismatch: " + filename)
    reg = json.loads((run / "registration.json").read_text())
    if reg["mode"] != "isolated" or reg["input_data_role"] != "consumed_development":
        raise ValueError("expected isolated development runs")
    metrics_list = json.loads((run / "metrics.json").read_text())
    metrics = {key(r): r for r in metrics_list}
    if len(metrics) != len(metrics_list):
        raise ValueError("duplicate metric key")
    if set(metrics) != {key(r) for r in summary["results"]} or len(metrics) != len(summary["results"]):
        raise ValueError("summary metric grid differs")
    for row in summary["results"]:
        m = metrics[key(row)]
        if row["engine_status"] != m["status"] or m["effective_split"] != "development":
            raise ValueError("metric status/split mismatch")
        for field in ("attempts", "submitted_orders", "full_fills", "partial_fills", "filled_shares", "fees_usd",
                      "extra_cost_usd", "ending_cash_usd", "min_available_cash_usd", "unresolved_positions"):
            if not math.isclose(float(row[field]), float(m[field]), abs_tol=1e-8, rel_tol=1e-10):
                raise ValueError("metric/summary mismatch: " + field)
        if row["recorded_scope_net_pnl_usd"] != m["net_pnl_usd"]:
            raise ValueError("recorded PnL mismatch")
        if row["run_key"] != completion["run_key"] or row["mode"] != "isolated":
            raise ValueError("cross-run result")
    daily_path = directory / "daily.csv"
    daily = list(csv.DictReader(daily_path.open()))
    return summary, reg, metrics, daily, {
        "report_path": str(path.resolve()), "report_sha256": sha(path),
        "daily_path": str(daily_path.resolve()), "daily_sha256": sha(daily_path),
        "source_run": str(run.resolve()), "completion_sha256": sha(run / "completion.json"),
        "registration_sha256": sha(run / "registration.json"), "metrics_sha256": sha(run / "metrics.json"),
        "coverage_sha256": sha(run / "coverage.json"), "run_key": completion["run_key"]}


def stats(rows):
    pnl = [r["recorded_scope_net_pnl_usd"] for r in rows if r["recorded_scope_net_pnl_usd"] is not None]
    result = {"registered_parameter_variants": len(rows),
              "complete_variants": sum(r["execution_complete_on_recorded_scope"] for r in rows),
              "evaluated_no_signal_variants": sum(r["engine_status"] == "evaluated_no_signal" for r in rows),
              "positive_variants": sum(p > 1e-10 for p in pnl),
              "negative_variants": sum(p < -1e-10 for p in pnl),
              "zero_variants": sum(abs(p) <= 1e-10 for p in pnl),
              "null_pnl_variants": len(rows) - len(pnl),
              "positive_on_both_prediction_dates_variants": sum(r["positive_on_both_prediction_dates"] for r in rows),
              "candidate_ids": [r["candidate_id"] for r in rows]}
    fields = ("recorded_scope_net_pnl_usd", "recorded_scope_pnl_per_filled_share_usd", "attempts", "filled_orders",
              "filled_shares", "fees_usd", "max_realized_drawdown_usd", "min_available_cash_usd")
    for field in fields:
        values = [r[field] for r in rows if r[field] is not None]
        for suffix, fn in (("min", min), ("median", statistics.median), ("max", max)):
            result[field + "_" + suffix] = fn(values) if values else None
    return result


def group_rows(rows, fields):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[f] for f in fields)].append(row)
    return [{**dict(zip(fields, k)), **stats(v)} for k, v in sorted(grouped.items())]


def paired_gates(rows):
    output = []
    for gate in ("reference_slope_gate", "smooth_only"):
        matchfields = tuple(f for f in FACTORS if f != gate) + ("scenario", "latency_ms")
        grouped = defaultdict(dict)
        for r in rows:
            k = tuple(r[f] for f in matchfields)
            if r[gate] in grouped[k]:
                raise ValueError("duplicate paired factor key")
            grouped[k][r[gate]] = r
        for k, variants in sorted(grouped.items()):
            if set(variants) != {False, True}:
                raise ValueError("missing matched gate pair")
            off, on = variants[False], variants[True]
            pnloff, pnlon = off["recorded_scope_net_pnl_usd"], on["recorded_scope_net_pnl_usd"]
            comparable = pnloff is not None and pnlon is not None
            output.append({"gate": gate, **dict(zip(matchfields, k)),
                "gate_off_candidate_id": off["candidate_id"], "gate_on_candidate_id": on["candidate_id"],
                "gate_off_pnl_usd": off["recorded_scope_net_pnl_usd"], "gate_on_pnl_usd": on["recorded_scope_net_pnl_usd"],
                "gate_on_minus_off_pnl_usd": pnlon - pnloff if comparable else None,
                "both_pnl_nonnull": comparable,
                "gate_off_status": off["engine_status"], "gate_on_status": on["engine_status"],
                "gate_on_minus_off_ending_cash_usd": on["ending_cash_usd"] - off["ending_cash_usd"],
                "gate_off_attempts": off["attempts"], "gate_on_attempts": on["attempts"],
                "gate_on_minus_off_attempts": on["attempts"] - off["attempts"],
                "gate_off_filled_orders": off["filled_orders"], "gate_on_filled_orders": on["filled_orders"],
                "gate_on_minus_off_filled_orders": on["filled_orders"] - off["filled_orders"],
                "same_pnl_attempts_fills": ((comparable and abs(pnlon - pnloff) <= 1e-10) or (pnlon is None and pnloff is None))
                    and on["attempts"] == off["attempts"] and on["filled_orders"] == off["filled_orders"]})
    return output


def gate_summary(pairs):
    grouped = defaultdict(list)
    fields = ("gate", "mechanism", "window", "scenario", "latency_ms")
    for row in pairs:
        grouped[tuple(row[f] for f in fields)].append(row)
    out = []
    for k, values in sorted(grouped.items()):
        diffs = [r["gate_on_minus_off_pnl_usd"] for r in values if r["gate_on_minus_off_pnl_usd"] is not None]
        out.append({**dict(zip(fields, k)), "matched_parameter_pairs": len(values),
            "both_pnl_nonnull_pairs": len(diffs), "at_least_one_null_pnl_pairs": len(values) - len(diffs),
            "gate_on_better_pairs": sum(x > 1e-10 for x in diffs), "gate_on_worse_pairs": sum(x < -1e-10 for x in diffs),
            "same_pnl_pairs": sum(abs(x) <= 1e-10 for x in diffs),
            "same_pnl_attempts_fills_pairs": sum(r["same_pnl_attempts_fills"] for r in values),
            "gate_on_minus_off_pnl_min_usd": min(diffs) if diffs else None,
            "gate_on_minus_off_pnl_median_usd": statistics.median(diffs) if diffs else None,
            "gate_on_minus_off_pnl_max_usd": max(diffs) if diffs else None,
            "gate_on_minus_off_ending_cash_min_usd": min(r["gate_on_minus_off_ending_cash_usd"] for r in values),
            "gate_on_minus_off_ending_cash_median_usd": statistics.median(r["gate_on_minus_off_ending_cash_usd"] for r in values),
            "gate_on_minus_off_ending_cash_max_usd": max(r["gate_on_minus_off_ending_cash_usd"] for r in values),
            "gate_on_minus_off_attempts_min": min(r["gate_on_minus_off_attempts"] for r in values),
            "gate_on_minus_off_attempts_max": max(r["gate_on_minus_off_attempts"] for r in values)})
    return out


def analyze(report_dirs, feature_coverage, source_review, out):
    reports = [read_report(p) for p in report_dirs]
    first_reg = reports[0][1]
    rules = {r["id"]: r for r in first_reg["strategies"]["candidate_rules"]}
    if len(rules) != 144 or {tuple(r[f] for f in FACTORS) for r in rules.values()} != set(itertools.product(*DOMAINS)):
        raise ValueError("not exact registered 144-rule factor grid")
    coverage = json.loads(Path(feature_coverage).read_text())
    evaluable_days = coverage["evaluable_dates_by_window"]
    if any(evaluable_days[str(w)] != ["2026-10-08", "2026-10-09"] for w in (15, 60, 120)):
        raise ValueError("feature-day scope differs; revisit stated two-day interpretation")
    rows = []
    configs = {}
    for summary, reg, metrics, daily, provenance in reports:
        if any(reg[f] != first_reg[f] for f in ("strategies", "cache_key", "events_sha256", "code_sha256")):
            raise ValueError("different strategy/input/core lineage")
        for s in reg["scenarios"]:
            if s["name"] in configs:
                raise ValueError("duplicate scenario")
            configs[s["name"]] = s["config"]
        daily_map = defaultdict(dict)
        for d in daily:
            k = d["candidate_id"], d["scenario"], int(d["latency_ms"])
            day = d["market_start_utc_date"]
            if day in daily_map[k]:
                raise ValueError("duplicate source daily row")
            daily_map[k][day] = d
        for source in summary["results"]:
            rule = rules[source["candidate_id"]]
            days = evaluable_days[str(rule["window"])]
            ds = daily_map[key(source)]
            if not set(days) <= ds.keys():
                raise ValueError("missing evaluable daily denominator")
            dpnl = {d: float(ds[d]["settled_subset_pnl_usd"]) for d in days}
            if not math.isclose(sum(dpnl.values()), source["settled_subset_pnl_usd"], abs_tol=1e-8):
                raise ValueError("evaluable dates do not reconcile to recorded PnL")
            selected_fields = ("candidate_id", "scenario", "latency_ms", "attempts", "submitted_orders", "filled_orders",
                "full_fills", "partial_fills", "failed_or_unfilled_attempts", "filled_shares", "fees_usd", "extra_cost_usd",
                "recorded_scope_net_pnl_usd", "recorded_scope_pnl_per_filled_share_usd", "max_realized_drawdown_usd",
                "min_available_cash_usd", "ending_cash_usd", "engine_status", "execution_complete_on_recorded_scope",
                "recorded_hours", "registered_markets", "complete_recording_dates", "net_pnl_usd")
            row = {f: source[f] for f in selected_fields}
            row.update({f: rule[f] for f in FACTORS})
            row.update(run_key=source["run_key"], requested_shares=configs[source["scenario"]]["shares"],
                prediction_dates=days, prediction_day_pnl_usd=dpnl,
                positive_on_both_prediction_dates=all(v > 0 for v in dpnl.values()),
                engine_valid_feature_rows_path_dependent=metrics[key(source)]["valid_feature_rows"],
                valid_feature_rows_before_first_signal=sum(coverage["by_market_start_utc_date"][d]["windows"][str(rule["window"])]["valid_rows"] for d in coverage["by_market_start_utc_date"]),
                formal_oos_result=None, qualified_strategy=False)
            rows.append(row)
    expected = set(itertools.product(rules, SCENARIOS, LATENCIES))
    if len(rows) != len(expected) or {key(r) for r in rows} != expected:
        raise ValueError("missing/duplicate full 2160-scenario grid")
    rows.sort(key=lambda r: key(r))
    cells = group_rows(rows, ("mechanism", "window", "reference_slope_gate", "smooth_only", "scenario", "latency_ms"))
    if len(cells) != 540 or any(c["registered_parameter_variants"] != 4 for c in cells):
        raise ValueError("factor cell missing band/edge variants")
    mechanism_windows = group_rows(rows, ("mechanism", "window", "scenario", "latency_ms"))
    pairs = paired_gates(rows)
    paired_summary = gate_summary(pairs)
    redundant = [r for r in pairs if r["gate"] == "reference_slope_gate" and r["window"] == 60
                 and r["mechanism"] in ("trend_pullback", "flat_overshoot")]
    if len(redundant) != 240 or not all(r["same_pnl_attempts_fills"] for r in redundant):
        raise ValueError("expected original 60-second slope gate equivalence not observed")
    baseline = [r for r in rows if r["scenario"] == SCENARIOS[0] and r["latency_ms"] == 500]
    baseline_mechanisms = group_rows(baseline, ("mechanism",))
    all15 = []
    for mechanism in DOMAINS[0]:
        ids = [cid for cid, r in rules.items() if r["mechanism"] == mechanism]
        positive_all = [cid for cid in ids if all(r["recorded_scope_net_pnl_usd"] is not None and r["recorded_scope_net_pnl_usd"] > 0 for r in rows if r["candidate_id"] == cid)]
        all15.append({"mechanism": mechanism, "registered_rules": len(ids),
            "rules_positive_in_all_15_alternative_scenarios": len(positive_all), "candidate_ids": positive_all})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    csvs = {"mar_factorial_all_rows.csv": rows, "mar_factorial_cells.csv": cells,
            "mar_mechanism_window_cells.csv": mechanism_windows, "mar_gate_matched_pairs.csv": pairs,
            "mar_gate_matched_summary.csv": paired_summary}
    for filename, data in csvs.items():
        csv_write(out / filename, data)
    value = {"schema": "mar-completed-full-factorial-diagnostics-v1", "new_replays": 0,
        "new_strategy_selections": 0, "formal_oos_result": None, "qualified_strategies": 0,
        "registered_rules": len(rules), "registered_factor_grid": dict(zip(FACTORS, DOMAINS)),
        "completed_scenario_rows": len(rows), "full_factor_cells": len(cells), "variants_per_cell": 4,
        "execution_status_counts": dict(Counter(r["engine_status"] for r in rows)),
        "paired_gate_rows": len(pairs), "matched_gate_summary_rows": len(paired_summary),
        "actual_60second_redundant_gate_check": {"paired_rows": len(redundant),
            "all_same_pnl_attempts_fills": True, "scope": "All 15 alternative execution scenarios for trend_pullback/flat_overshoot at window=60; no new replay."},
        "source_reports": [r[4] for r in reports], "cache_key": first_reg["cache_key"],
        "events_sha256": first_reg["events_sha256"], "executed_core_sha256": first_reg["code_sha256"],
        "analysis_code_sha256": sha(__file__), "source_review_sha256": sha(source_review),
        "feature_coverage_path": str(Path(feature_coverage).resolve()), "feature_coverage_sha256": sha(feature_coverage),
        "feature_coverage": coverage, "scenario_configs": configs,
        "table_files": {n: {"path": str((out / n).resolve()), "sha256": sha(out / n), "rows": len(v)} for n, v in csvs.items()},
        "baseline_5shares_500ms_all_mechanisms": baseline_mechanisms,
        "all_scenario_robustness_counts": all15,
        "baseline_5shares_500ms_full_cells": [r for r in cells if r["scenario"] == SCENARIOS[0] and r["latency_ms"] == 500],
        "baseline_5shares_500ms_gate_comparison": [r for r in paired_summary if r["scenario"] == SCENARIOS[0] and r["latency_ms"] == 500],
        "uncertainty_policy": {"model_evaluable_dates": ["2026-10-08", "2026-10-09"],
            "excluded_warmup": ["2026-10-07"], "coverage_and_missing_denominators_include_warmup": True,
            "factor_cell_ranges_and_medians_are_not_confidence_intervals": True,
            "model_ci_not_recomputed_here": "Use two-prediction-day intervals in completed MAR assessments; no inferential tests across correlated parameter variants.",
            "parameter_pairs_are_not_independent_samples": True},
        "static_rule_interpretation": {
            "z_window": "Current locally received BTC price minus trailing 15/60/120-second time-grid mean, divided by max(trailing price standard deviation, 1bp of mean).",
            "slope_window": "Change in that trailing BTC mean over ten seconds, in bps.",
            "reference_slope_gate": "All refslope aliases refer to the same BTC 60-second observed-price mean change over ten seconds. This is NOT official settlement TWAP.",
            "60_second_gate_redundancy": "For window=60, trend_pullback s*slope60>0.5 already implies s*refslope60>0; flat_overshoot abs(slope60)<=0.5 is identical to its extra refslope60 gate. These on/off rules are structurally redundant given frozen enrichment. btc_jump_poly_lag has no base slope-window gate, so its extra slope gate is not redundant.",
            "gate_off_availability": "Frozen required_features still require refslope and poly_eff even with their gates off; changing availability requirements would define new rules.",
            "smoothness": "Trailing Poly 15-second path efficiency abs(net move)/sum(abs(increments)); gate requires >=0.7 and a move in the selected trade direction. Not a smooth-only market selected using future path.",
            "smooth_features_not_execution_prices": "Any smoothed probability/derivative is a model feature. Original tick-staircase order books, fixed limit prices and delayed matching prices remain unchanged. Continuity of a fitted curve does not establish a tradable edge.",
            "band": "Both 0.5 and 1.0 bands retained. trend_pullback tests abs(z)<=band; flat_overshoot tests directional z<-band; jump-lag tests directional z>=band."},
        "hypothesis_assessment": {
            "general_balance_or_overshoot_law_established": False,
            "reason": "These are executions of compound, development-selected policies with probability-edge, timing and feature-availability gates on two partially recorded prediction dates. They do not isolate the conditional information value of MA distance or establish a general balance/overextension law.",
            "interpretation": "Near a mean can occur in balanced trading or a trend pullback. Being far from a mean can occur in continuation or reversal. The rule grid deliberately separates trend-pullback, flat-overshoot and jump-with-Poly-lag; no universal reversal assumption is justified.",
            "research_scope_mapping": {"trend_poly_lag_continuation_or_jump": "btc_jump_poly_lag, with trend_pullback requiring recent BTC continuation; these are taker policies using a probability-edge gate.",
                "near_mean_or_overshoot_reversion": "trend_pullback uses near-mean distance in an established trend; flat_overshoot uses opposite directional distance in a low mean-slope regime.",
                "maker_hedge_or_hybrid": "Not implemented by these frozen long-taker hold-to-official-settlement rules; cannot infer maker/hedge profitability from these runs."},
            "unresolved_tests": ["Official settlement TWAP slope versus no-slope comparison requires official TWAP samples.",
                "Unconditional distance-to-MA information/ablation needs predeclared conditional evaluation with common eligible denominators and strictly later untouched coverage.",
                "True aggressor-side volume, stateful scale-in, maker queue/hedging were not tested in these frozen long-taker hold-to-settlement rules.",
                "Seven complete days and later untouched OOS are absent; parameter medians and two-day positive PnL do not establish significance."]},
        "accounting_policy": "PnL is never summed across rules, gates, windows, sizes or latencies. Each scenario is an alternative isolated $1000 simulation. Min/median/max describe correlated candidate outcomes. Pair deltas compare complete policies, including changed first-signal times and fills, not a causal treatment effect.",
        "null_policy": "evaluated_no_signal retains original null recorded-scope PnL and zero actual cash change; never impute zero PnL. PnL distributions exclude null and show explicit no-signal/null counts. Paired PnL deltas require both PnLs nonnull; ending-cash deltas are separately reported for all pairs.",
        "capacity_policy": "Displayed depth is a fill/capacity upper bound, not observed execution. plus_1cent is per-filled-share extra cash cost; half_depth is depth sensitivity. Realized drawdown is not mark-to-market.",
        "window_comparison_limit": "The 15-second feature window has 20,614 eligible decisions versus 20,689 for 60/120 seconds, so window comparisons include eligibility differences. Gate-on/off comparisons preserve the same required-feature pool for a given window, but may change first signals, directions and fills.",
        "full_calendar_pnl": None, "original_ledgers_or_summaries_modified": False}
    names = {"trend_pullback": "趋势回踩", "flat_overshoot": "平盘过冲", "btc_jump_poly_lag": "BTC跳变＋Poly滞后"}
    md = ["# MAR 全参数对照（已完成结果的只读分析）", "",
          "144条冻结规则、2160个已完成执行情景；本分析新增回放0次，新增选策0次。540个完整因子单元各含4个band/edge参数，另有2160组成对gate比较。参数变体、执行情景及经济机制不可互换。", "",
          "源录制共27.01591小时、跨Oct7/8/9三个UTC日期，336登记市场；只有Oct8/9两天有完整模型特征，各132场。Oct7是无预测warmup。没有完整七天、没有新OOS结果。", "",
          "以下统一为5份、500ms、原深度、基础费用。同一行的最小值/中位数/最大值描述相关参数结果，**不是收益相加，也不是置信区间**。无信号的原PnL保持null，不填零；有尝试但净收益零与无信号单列。", "",
          "| 机制 | BTC均线窗口 | 参数数 | 正 / 负 / 零 / 无信号 | 非null PnL最小 / 中位 / 最大（美元） |",
          "|---|---:|---:|---|---|" ]
    for r in mechanism_windows:
        if r["scenario"] == SCENARIOS[0] and r["latency_ms"] == 500:
            pnl = " / ".join(f"{r['recorded_scope_net_pnl_usd_' + s]:+.5f}" if r['recorded_scope_net_pnl_usd_' + s] is not None else "null" for s in ("min", "median", "max"))
            counts = " / ".join(str(r[k]) for k in ("positive_variants", "negative_variants", "zero_variants", "evaluated_no_signal_variants"))
            md.append(f"| {names[r['mechanism']]} | {r['window']}秒 | {r['registered_parameter_variants']} | {counts} | {pnl} |")
    md += ["", "全部15个尺寸/延迟/成本替代情景都为正的规则数：" + "；".join(f"{names[r['mechanism']]} {r['rules_positive_in_all_15_alternative_scenarios']}/{r['registered_rules']}" for r in all15) + "。这些仍是已消耗开发数据上的相关变体，不能升级为已确认独立策略。", "",
           "gate比较保留所有配对；一边无信号时PnL差值为null，现金变化另列。smooth gate开关只改变信号条件；关闭后仍要求poly_eff、poly_move等冻结输入。reference slope gate关闭也仍要求refslope。", "",
           "15/60/120秒是BTC本地接收价的历史均线窗口；slope是该均线在10秒内的变化。三个refslope别名均是同一个BTC 60秒均线斜率，**不是官方结算TWAP**。60秒窗口中，趋势回踩和平盘过冲的额外reference slope gate逻辑已被原条件包含，开关冗余不能视为独立证据。", "",
           "平滑的是特征或估计概率；原盘口tick台阶、固定限价和延迟撮合价格都保留。平滑曲线连续或能算高阶导数，并不自动形成可成交优势。", "",
           "这些结果不支持把“离均线远”统一解释为反转：本轮15秒趋势回踩16/16亏损，60秒平盘过冲16/16亏损；BTC跳变与Poly滞后的部分规则盈利，也可能是延续而非回归。当前只比较有概率优势、时间和特征门槛的复合策略，未单独识别均线距离的信息增量。", "",
           "研究边界：①趋势＋Poly滞后/延续跳变；②趋势回踩与低斜率过冲回归，均有本轮taker结果；③maker、双腿对冲、状态化加仓及其混合尚不在这批冻结规则内。缺真实主动成交方向，不能伪造量价流。官方TWAP有/无斜率比较仍缺官方样本。", "",
           "两天均为部分录制且已参与开发；不能证明general规律、统计显著性或月收益。15秒特征资格20614行，60/120秒20689行，窗口比较也包含资格池差别。完整CSV保留所有失败、无信号、费用、资金占用与净收益；不追加调参、选优或组合。", "",
           "工件：mar_factorial_all_rows.csv（2160原情景）、mar_factorial_cells.csv（540全因子单元）、mar_mechanism_window_cells.csv（135窗口单元）、mar_gate_matched_pairs.csv（2160配对）、mar_gate_matched_summary.csv（270配对分组）；源hash与覆盖证明见mar_factorial_diagnostics.json。", ""]
    md_path = out / "mar_factorial_readout_zh.md"
    md_path.write_text("\n".join(md))
    value["readout"] = {"path": str(md_path.resolve()), "sha256": sha(md_path)}
    (out / "mar_factorial_diagnostics.json").write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return value


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reports", nargs=3, required=True)
    p.add_argument("--feature-coverage", required=True)
    p.add_argument("--source-review", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    result = analyze(a.reports, a.feature_coverage, a.source_review, a.out)
    print(json.dumps({"rows": result["completed_scenario_rows"], "factor_cells": result["full_factor_cells"],
        "paired_gate_rows": result["paired_gate_rows"], "new_replays": 0, "out": a.out}))
