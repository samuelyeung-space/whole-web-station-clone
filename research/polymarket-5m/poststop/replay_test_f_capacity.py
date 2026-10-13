#!/usr/bin/env python3
"""Post-stop execution sensitivity for the frozen Test F trade ledger.

This does not alter Test F or create a new formal verdict. It applies fixed
requested sizes to the already consumed 1,200-row stopping ledger under both
full-or-zero and displayed-depth partial-FAK counterfactuals, using one shared
USD 1,000 cash account. Cash is released at the scheduled five-minute market
close, which is explicitly optimistic because actual redemption receipt
timestamps are unavailable.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scipy.stats import t as student_t


ROOT = Path(__file__).resolve().parent


def market_close(market_id: str) -> float:
    try:
        start = int(market_id.rsplit("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"bad market id: {market_id}") from exc
    return float(start + 300)


def load_rows(input_path: Path) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    with input_path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    "won": float(raw["won"]),
                    "price": float(raw["price"]),
                    "fee": float(raw["fee"]),
                    "pnl": float(raw["pnl"]),
                    "size": float(raw["size"]),
                    "t": float(raw["t"]),
                    "market_id": raw["market_id"],
                    "run": raw["run"],
                    "lag": float(raw["lag"]),
                }
            )
    rows.sort(key=lambda row: (float(row["t"]), str(row["market_id"])))
    return rows


def replay(
    rows: list[dict[str, float | str]],
    qty: float,
    extra_cost: float,
    fill_mode: str = "full_or_zero",
) -> dict[str, object]:
    if fill_mode not in {"full_or_zero", "partial_displayed_fak"}:
        raise ValueError(f"unknown fill mode: {fill_mode}")
    cash = 1000.0
    min_cash = cash
    open_positions: list[dict[str, float]] = []
    fills = 0
    full_fills = 0
    partial_fills = 0
    depth_failures = 0
    cash_rejections = 0
    gross_shares = 0.0
    unfilled_requested_shares = 0.0
    realized_pnl = 0.0
    peak_realized = 0.0
    max_realized_drawdown = 0.0
    pnl_by_day: dict[str, float] = defaultdict(float)
    fills_by_day: dict[str, int] = defaultdict(int)
    per_share_pnl_by_day: dict[str, list[tuple[float, float]]] = defaultdict(list)

    def settle(until: float) -> None:
        nonlocal cash, realized_pnl, peak_realized, max_realized_drawdown
        still_open: list[dict[str, float]] = []
        for position in open_positions:
            if position["settle_time"] <= until:
                cash += position["payout"]
                trade_pnl = position["payout"] - position["cost"]
                realized_pnl += trade_pnl
                pnl_by_day[position["day"]] += trade_pnl
                peak_realized = max(peak_realized, realized_pnl)
                max_realized_drawdown = max(
                    max_realized_drawdown, peak_realized - realized_pnl
                )
            else:
                still_open.append(position)
        open_positions[:] = still_open

    for row in rows:
        now = float(row["t"])
        settle(now)
        displayed_size = max(0.0, float(row["size"]))
        if fill_mode == "full_or_zero":
            if displayed_size + 1e-9 < qty:
                depth_failures += 1
                unfilled_requested_shares += qty
                continue
            fill_qty = qty
        else:
            fill_qty = min(qty, displayed_size)
            if fill_qty <= 1e-9:
                depth_failures += 1
                unfilled_requested_shares += qty
                continue
        per_share_cost = float(row["price"]) + float(row["fee"]) + extra_cost
        # A submitted FAK buy must be collateralized for the requested order,
        # even when only displayed depth is assumed to fill. Unspent collateral
        # is treated as immediately released after the FAK response.
        authorized_cost = qty * per_share_cost
        if cash + 1e-9 < authorized_cost:
            cash_rejections += 1
            unfilled_requested_shares += qty
            continue
        cost = fill_qty * per_share_cost
        cash -= cost
        min_cash = min(min_cash, cash)
        day = datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()
        open_positions.append(
            {
                "settle_time": market_close(str(row["market_id"])),
                "payout": fill_qty * float(row["won"]),
                "cost": cost,
                "day": day,
            }
        )
        fills += 1
        if fill_qty + 1e-9 >= qty:
            full_fills += 1
        else:
            partial_fills += 1
        gross_shares += fill_qty
        unfilled_requested_shares += qty - fill_qty
        fills_by_day[day] += 1
        per_share_pnl_by_day[day].append(
            (
                float(row["won"])
                - float(row["price"])
                - float(row["fee"])
                - extra_cost,
                fill_qty,
            )
        )

    settle(math.inf)
    weighted_per_share_values = [
        (value, shares)
        for values in per_share_pnl_by_day.values()
        for value, shares in values
    ]
    mean_per_share = (
        sum(value * shares for value, shares in weighted_per_share_values)
        / gross_shares
        if gross_shares
        else None
    )
    cluster_count = len(per_share_pnl_by_day)
    cluster_se = None
    one_sided_p = None
    lower_99 = None
    if mean_per_share is not None and cluster_count >= 2:
        centered_cluster_sums = [
            sum((value - mean_per_share) * shares for value, shares in values)
            for values in per_share_pnl_by_day.values()
        ]
        cluster_se = math.sqrt(
            cluster_count
            / (cluster_count - 1)
            * sum(value * value for value in centered_cluster_sums)
        ) / gross_shares
        if cluster_se > 0:
            statistic = mean_per_share / cluster_se
            one_sided_p = float(student_t.sf(statistic, df=cluster_count - 1))
            lower_99 = mean_per_share - float(
                student_t.ppf(0.99, df=cluster_count - 1)
            ) * cluster_se
    return {
        "requested_shares": qty,
        "extra_cost_per_share": extra_cost,
        "fill_mode": fill_mode,
        "signals": len(rows),
        "orders_with_any_fill": fills,
        "full_fills": full_fills,
        "partial_fills": partial_fills,
        "depth_failures": depth_failures,
        "cash_rejections": cash_rejections,
        "filled_shares": gross_shares,
        "unfilled_requested_shares": unfilled_requested_shares,
        "net_pnl_usd": cash - 1000.0,
        "net_per_filled_share_usd": (
            (cash - 1000.0) / gross_shares if gross_shares else None
        ),
        "day_clustered_standard_error_per_share_usd": cluster_se,
        "day_clustered_one_sided_p": one_sided_p,
        "one_sided_99pct_day_clustered_lower_bound_per_share_usd": lower_99,
        "ending_cash_usd": cash,
        "minimum_available_cash_usd": min_cash,
        "maximum_realized_pnl_drawdown_usd": max_realized_drawdown,
        "days_with_fills": len(fills_by_day),
        "fills_by_utc_signal_day": dict(sorted(fills_by_day.items())),
        "pnl_by_utc_signal_day": {
            day: value for day, value in sorted(pnl_by_day.items())
        },
        "cash_release_assumption": "scheduled market start + 300s; optimistic proxy, not redemption receipt",
        "execution_assumption": (
            "full-or-zero against recorded top ask size; not a private FAK receipt"
            if fill_mode == "full_or_zero"
            else "partial fill equals min(requested size, recorded top ask size); not a private FAK receipt"
        ),
        "cash_authorization_assumption": "cash gate uses the full requested buy notional; unspent FAK collateral is immediately released",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = load_rows(args.input)
    if len(rows) != 1200:
        raise SystemExit(f"expected frozen 1,200 rows, found {len(rows)}")
    if len({str(row["market_id"]) for row in rows}) != len(rows):
        raise SystemExit("Test F stopping ledger is not one row per market")
    if {float(row["lag"]) for row in rows} != {0.3}:
        raise SystemExit("unexpected Test F lag")
    frozen_mean = sum(float(row["pnl"]) for row in rows) / len(rows)
    sizes = sorted(float(row["size"]) for row in rows)
    result = {
        "status": "post_stop_execution_sensitivity_not_formal_verdict",
        "input_rows": len(rows),
        "unique_markets": len({str(row["market_id"]) for row in rows}),
        "unique_runs": len({str(row["run"]) for row in rows}),
        "frozen_mean_pnl_per_share_usd": frozen_mean,
        "displayed_ask_size": {
            "minimum": sizes[0],
            "median": sizes[len(sizes) // 2],
            "p90": sizes[int(0.9 * (len(sizes) - 1))],
            "maximum": sizes[-1],
        },
        "scenarios": [
            replay(rows, qty, extra_cost, fill_mode)
            for qty in (5.0, 10.0, 20.0, 100.0)
            for extra_cost in (0.0, 0.01)
            for fill_mode in ("full_or_zero", "partial_displayed_fak")
        ],
        "limitations": [
            "Test F was frozen and judged at one-share economics and 300ms; this sensitivity was computed after the pass was observed.",
            "Displayed top-level ask size is only a counterfactual capacity upper bound.",
            "Partial FAK scenarios assume every displayed top-level share fills at the recorded price, so they remain optimistic and do not identify queue competition or stale depth.",
            "No private 100-share FAK receipts, queue priority, impact, or actual redemption timing are available.",
            "The 300ms execution horizon is not the 500/600/1000ms default stress grid.",
            "These size and cost scenarios are dependent alternatives and must not be summed.",
        ],
    }
    if args.output is not None:
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
