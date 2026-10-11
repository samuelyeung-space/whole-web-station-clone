"""Chronological legacy-tape sensitivity for EPM-matched_market-02.

Fit the already-fixed eight-leaf market-only residual model on one earlier
recording and score one later recording.  This is deliberately *not* formal
OOS: both archives are historical/public, Gamma metadata lacks a local receipt
clock, and the legacy probability tape records only the Up top of book.  Down
is therefore the established legacy mirror (ask=1-Up bid, size=Up bid size).

The replay is still causal on the recorded local receipt clock: decisions are
on a fixed one-second grid, books must be no older than 250 ms, limits are fixed
at the decision, and matching occurs exactly 500/600/1000 ms later with FAK
partial fills and no retry.  The cash ledger shares USD 1,000 across markets.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

import mine500_probability_models as prior
import mine500_tree_models as trees


VERSION = "historical-epm-chronological-sensitivity-v1"
CANDIDATE_ID = "EPM-matched_market-02"
LATENCIES_MS = (500, 600, 1000)
REQUESTED_SHARES = 100.0
INITIAL_CASH_USD = 1000.0
MARGIN = 0.02
FEE_RATE = 0.07
GRID_MS = 1000
MAX_QUOTE_AGE_MS = 250
FEE_QUANTUM = Decimal("0.00001")


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _fee(shares: float, price: float) -> Decimal:
    q, p, r = (Decimal(str(x)) for x in (shares, price, FEE_RATE))
    return q * r * p * (Decimal(1) - p)


def rounded_fee(shares: float, price: float) -> float:
    value = _fee(shares, price)
    if value < FEE_QUANTUM:
        return 0.0
    return float(value.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP))


def reserved_fee(shares: float, price: float) -> float:
    return float(_fee(shares, price).quantize(FEE_QUANTUM, rounding=ROUND_CEILING))


def _top_size(raw: str) -> float:
    try:
        levels = json.loads(raw)
        value = float(levels[0][1])
        return value if math.isfinite(value) and value >= 0 else math.nan
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return math.nan


def load_gamma(bundle: Path) -> dict[str, dict]:
    files = list(bundle.glob("bundle-btc/data/polymarket/daily/markets/BTC-5m/*.jsonl.gz"))
    if len(files) != 1:
        raise ValueError("expected exactly one BTC-5m Gamma metadata member")
    result = {}
    with gzip.open(files[0], "rt") as handle:
        for line in handle:
            row = json.loads(line)
            raw = row.get("raw", {})
            prices = row.get("outcome_prices") or raw.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if not prices or len(prices) != 2:
                raise ValueError("missing Gamma outcome-price pair")
            binary = prices in (["1", "0"], ["0", "1"], [1, 0], [0, 1])
            closed = raw.get("closedTime")
            closed_ms = int(pd.Timestamp(closed).timestamp() * 1000) if closed else None
            result[str(row["slug"])] = {
                "winner": ("Up" if float(prices[0]) == 1 else "Down") if binary else None,
                "gamma_closed_ms": closed_ms,
            }
    return result


def load_tape(bundle: Path) -> tuple[pd.DataFrame, dict[str, dict], dict]:
    latency = bundle / "bundle-btc/latency"
    registry = pd.read_csv(latency / "runtime_1000.market_registry.csv.gz")
    if registry["market_id"].duplicated().any():
        raise ValueError("duplicate market registry row")
    starts = dict(zip(registry["market_id"].astype(str), registry["start_ts"].astype(float)))
    use = ["market_id", "source_ts", "receive_ts", "best_bid", "best_ask", "bids_json", "asks_json"]
    parts = []
    input_rows = valid_rows = 0
    for chunk in pd.read_csv(latency / "runtime_1000.poly_probability_observations_v1.csv.gz",
                             usecols=use, chunksize=200_000):
        input_rows += len(chunk)
        chunk["market_id"] = chunk["market_id"].astype(str)
        chunk["start_ts"] = chunk["market_id"].map(starts)
        good = (chunk["start_ts"].notna() & chunk["receive_ts"].notna()
                & chunk["source_ts"].notna() & chunk["best_bid"].notna()
                & chunk["best_ask"].notna() & (chunk["best_bid"] >= 0)
                & (chunk["best_ask"] <= 1) & (chunk["best_bid"] < chunk["best_ask"])
                & (chunk["receive_ts"] >= chunk["start_ts"])
                & (chunk["receive_ts"] < chunk["start_ts"] + 300))
        chunk = chunk.loc[good].copy()
        valid_rows += len(chunk)
        chunk["bid_size"] = [_top_size(x) for x in chunk["bids_json"]]
        chunk["ask_size"] = [_top_size(x) for x in chunk["asks_json"]]
        chunk = chunk.loc[np.isfinite(chunk["bid_size"]) & np.isfinite(chunk["ask_size"])]
        parts.append(chunk.drop(columns=["bids_json", "asks_json"]))
    tape = pd.concat(parts, ignore_index=True)
    tape["recv_ms"] = np.rint(tape["receive_ts"].to_numpy() * 1000).astype(np.int64)
    tape["source_ms"] = np.rint(tape["source_ts"].to_numpy() * 1000).astype(np.int64)
    tape["start_ms"] = np.rint(tape["start_ts"].to_numpy() * 1000).astype(np.int64)
    tape["end_ms"] = tape["start_ms"] + 300_000
    tape.sort_values(["market_id", "recv_ms", "source_ms"], kind="mergesort", inplace=True)
    tape.drop_duplicates(["market_id", "recv_ms"], keep="last", inplace=True)
    tape.reset_index(drop=True, inplace=True)
    gamma = load_gamma(bundle)
    if set(starts) != set(gamma):
        raise ValueError("registry/Gamma market mismatch")
    outcomes = pd.read_csv(latency / "runtime_1000.market_outcomes.csv.gz")
    conflicts = []
    for row in outcomes.itertuples(index=False):
        if gamma[str(row.market_id)]["winner"] != str(row.winner):
            conflicts.append(str(row.market_id))
    if conflicts:
        raise ValueError("Gamma/runtime outcome conflict")
    audit = {
        "input_observation_rows": input_rows,
        "valid_active_rows_before_dedup": valid_rows,
        "causal_rows_after_same_receipt_dedup": len(tape),
        "registry_markets": len(starts),
        "runtime_outcomes": len(outcomes),
        "gamma_markets": len(gamma),
        "retrospective_gamma_outcomes": sum(x["winner"] is not None for x in gamma.values()),
        "outcome_conflicts": conflicts,
        "receive_min_ms": int(tape["recv_ms"].min()),
        "receive_max_ms": int(tape["recv_ms"].max()),
    }
    return tape, gamma, audit


def decision_grid(tape: pd.DataFrame) -> pd.DataFrame:
    grid = tape.copy()
    grid["decision_ms"] = ((grid["recv_ms"] + GRID_MS - 1) // GRID_MS) * GRID_MS
    grid = grid.loc[(grid["decision_ms"] < grid["end_ms"])
                    & (grid["decision_ms"] - grid["recv_ms"] <= MAX_QUOTE_AGE_MS)]
    grid.sort_values(["market_id", "decision_ms", "recv_ms", "source_ms"], kind="mergesort", inplace=True)
    grid.drop_duplicates(["market_id", "decision_ms"], keep="last", inplace=True)
    grid["mid_up"] = (grid["best_bid"] + grid["best_ask"]) / 2
    grid["tau_s"] = (grid["end_ms"] - grid["decision_ms"]) / 1000
    return grid.reset_index(drop=True)


def _vector(mid: float, tau: float) -> np.ndarray:
    return prior.feature_vector({"mid_up": float(mid), "tau_s": float(tau)}, "market_calibration")


def fit_model(grid: pd.DataFrame, gamma: dict[str, dict]):
    resolved = {m for m, value in gamma.items() if value["winner"] is not None}
    rows = grid.loc[grid["market_id"].isin(resolved)].copy()
    rows["target"] = [1.0 if gamma[m]["winner"] == "Up" else 0.0 for m in rows["market_id"]]
    counts = rows.groupby("market_id").size().to_dict()
    weights = np.asarray([1.0 / counts[m] for m in rows["market_id"]])
    x = np.asarray([_vector(m, t) for m, t in zip(rows["mid_up"], rows["tau_s"])])
    y = rows["target"].to_numpy() - rows["mid_up"].to_numpy()
    model = HistGradientBoostingRegressor(**trees.FIXED_PARAMETERS, max_leaf_nodes=8)
    with threadpool_limits(limits=1):
        model.fit(x, y, sample_weight=weights)
    if model.n_iter_ != trees.FIXED_PARAMETERS["max_iter"]:
        raise ValueError("unexpected early stopping")
    descriptor = {
        "candidate_id": CANDIDATE_ID,
        "model_id": "matched_market",
        "parameters": dict(trees.FIXED_PARAMETERS, max_leaf_nodes=8),
        "training_markets": len(counts),
        "training_rows": len(rows),
        "training_class_markets": dict(Counter(gamma[m]["winner"] for m in counts)),
        "weight_sum_by_market_min": min(sum(weights[rows["market_id"].to_numpy() == m]) for m in counts),
        "weight_sum_by_market_max": max(sum(weights[rows["market_id"].to_numpy() == m]) for m in counts),
        "learned_state_hash": stable_hash(trees._learned_state(model)),
        "feature_vector": "existing matched_market == market_calibration vector",
        "target": "retrospective official Up indicator minus received Up midpoint",
    }
    descriptor["model_hash"] = stable_hash(descriptor)
    return model, descriptor


def score_grid(model, grid: pd.DataFrame) -> pd.DataFrame:
    result = grid.copy()
    x = np.asarray([_vector(m, t) for m, t in zip(result["mid_up"], result["tau_s"])])
    with threadpool_limits(limits=1):
        residual = model.predict(x)
    result["p_up"] = np.clip(result["mid_up"].to_numpy() + residual, .001, .999)
    result["ask_up"] = result["best_ask"]
    result["ask_down"] = 1 - result["best_bid"]
    up_fee = FEE_RATE * result["ask_up"] * (1 - result["ask_up"])
    down_fee = FEE_RATE * result["ask_down"] * (1 - result["ask_down"])
    result["up_edge"] = result["p_up"] - result["ask_up"] - up_fee
    result["down_edge"] = 1 - result["p_up"] - result["ask_down"] - down_fee
    result["edge"] = result[["up_edge", "down_edge"]].max(axis=1)
    result["side"] = np.where(result["up_edge"] >= result["down_edge"], "Up", "Down")
    signal = result.loc[result["edge"] >= MARGIN].copy()
    signal.sort_values(["decision_ms", "market_id"], kind="mergesort", inplace=True)
    signal = signal.drop_duplicates("market_id", keep="first")
    return signal.reset_index(drop=True)


def potential_attempts(signals: pd.DataFrame, tape: pd.DataFrame, gamma: dict[str, dict], latency_ms: int):
    groups = {m: g.reset_index(drop=True) for m, g in tape.groupby("market_id", sort=False)}
    attempts = []
    for row in signals.itertuples(index=False):
        side = str(row.side)
        limit = float(row.ask_up if side == "Up" else row.ask_down)
        match_ms = int(row.decision_ms) + latency_ms
        market = groups[str(row.market_id)]
        index = int(np.searchsorted(market["recv_ms"].to_numpy(), match_ms, side="right") - 1)
        status = "no_fill"
        fill = price = fee = cost = 0.0
        quote_recv_ms = None
        if match_ms >= int(row.end_ms):
            status = "rejected_market_closed_at_match"
        elif index < 0:
            status = "rejected_missing_book_at_match"
        else:
            quote = market.iloc[index]
            quote_recv_ms = int(quote.recv_ms)
            if not 0 <= match_ms - quote_recv_ms <= MAX_QUOTE_AGE_MS:
                status = "rejected_stale_book_at_match"
            else:
                price = float(quote.best_ask if side == "Up" else 1 - quote.best_bid)
                size = float(quote.ask_size if side == "Up" else quote.bid_size)
                if price <= limit + 1e-9:
                    fill = min(REQUESTED_SHARES, size)
                    if fill > 1e-9:
                        fee = rounded_fee(fill, price)
                        cost = fill * price + fee
                        status = "filled_full" if fill >= REQUESTED_SHARES - 1e-9 else "filled_partial"
        outcome = gamma[str(row.market_id)]
        payout = fill if fill and outcome["winner"] == side else 0.0
        reservation = REQUESTED_SHARES * limit + reserved_fee(REQUESTED_SHARES, limit)
        attempts.append({
            "candidate_id": CANDIDATE_ID, "market_id": str(row.market_id),
            "decision_ms": int(row.decision_ms), "match_ms": match_ms,
            "latency_ms": latency_ms, "side": side, "p_up": float(row.p_up),
            "edge": float(row.edge), "limit_price": limit,
            "decision_book_recv_ms": int(row.recv_ms), "match_book_recv_ms": quote_recv_ms,
            "requested_shares": REQUESTED_SHARES, "potential_status": status,
            "potential_filled_shares": fill, "fill_price": price if fill else None,
            "fees_usd": fee, "potential_cost_usd": cost, "potential_payout_usd": payout,
            "winner": outcome["winner"], "settlement_ms": int(outcome["gamma_closed_ms"]),
            "reserved_usd_at_decision": reservation,
        })
    return attempts


def cash_replay(rows: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    events = [(r["decision_ms"], 0, i, "decision") for i, r in enumerate(rows)]
    heapq.heapify(events)
    cash, reserved = INITIAL_CASH_USD, 0.0
    realized = 0.0
    peak_realized_equity, max_drawdown = INITIAL_CASH_USD, 0.0
    min_available = INITIAL_CASH_USD
    counts, ledger = Counter(), []
    daily = defaultdict(float)
    for r in rows:
        r.update(submitted=False, status="pending", filled_shares=0.0,
                 cost_usd=0.0, payout_usd=0.0, net_pnl_usd=0.0)
    while events:
        when, _, index, kind = heapq.heappop(events)
        row = rows[index]
        if kind == "decision":
            need = row["reserved_usd_at_decision"]
            if cash - reserved + 1e-9 < need:
                row["status"] = "rejected_insufficient_cash"
                counts[row["status"]] += 1
            else:
                row["submitted"] = True
                row["status"] = "scheduled"
                reserved += need
                counts["submitted"] += 1
                heapq.heappush(events, (row["match_ms"], 1, index, "match"))
        elif kind == "match":
            reserved -= row["reserved_usd_at_decision"]
            row["status"] = row["potential_status"]
            row["filled_shares"] = row["potential_filled_shares"]
            row["cost_usd"] = row["potential_cost_usd"]
            cash -= row["cost_usd"]
            counts[row["status"]] += 1
            if row["filled_shares"]:
                settle = max(row["settlement_ms"], row["match_ms"])
                heapq.heappush(events, (settle, 2, index, "settlement"))
        else:
            row["payout_usd"] = row["potential_payout_usd"]
            cash += row["payout_usd"]
            row["net_pnl_usd"] = row["payout_usd"] - row["cost_usd"]
            realized += row["net_pnl_usd"]
            peak_realized_equity = max(peak_realized_equity, INITIAL_CASH_USD + realized)
            max_drawdown = max(max_drawdown, peak_realized_equity - INITIAL_CASH_USD - realized)
            day = datetime.fromtimestamp(row["decision_ms"] / 1000, timezone.utc).date().isoformat()
            daily[day] += row["net_pnl_usd"]
            counts["settled"] += 1
        if min(cash - reserved, reserved) < -1e-6:
            raise AssertionError("negative cash or reservation")
        min_available = min(min_available, cash - reserved)
        ledger.append({"ts_ms": when, "event": kind, "market_id": row["market_id"],
                       "cash_usd": cash, "reserved_usd": reserved,
                       "available_usd": cash - reserved, "realized_pnl_usd": realized})
    pnl = math.fsum(r["net_pnl_usd"] for r in rows)
    if abs(pnl - realized) > 1e-7 or abs(cash - INITIAL_CASH_USD - pnl) > 1e-7:
        raise AssertionError("cash/PnL reconciliation failed")
    fills = [r for r in rows if r["filled_shares"] > 0]
    metrics = {
        "candidate_id": CANDIDATE_ID,
        "latency_ms": rows[0]["latency_ms"] if rows else None,
        "signals": len(rows), "submitted_orders": counts["submitted"],
        "status_counts": dict(counts),
        "filled_orders": len(fills),
        "full_fills": sum(r["status"] == "filled_full" for r in rows),
        "partial_fills": sum(r["status"] == "filled_partial" for r in rows),
        "filled_shares": math.fsum(r["filled_shares"] for r in rows),
        "fees_usd": math.fsum(r["fees_usd"] for r in rows if r["submitted"]),
        "net_pnl_usd": pnl,
        "pnl_per_filled_share_usd": pnl / math.fsum(r["filled_shares"] for r in rows) if fills else None,
        "initial_cash_usd": INITIAL_CASH_USD,
        "ending_cash_usd": cash,
        "minimum_available_cash_usd": min_available,
        "max_realized_drawdown_usd": max_drawdown,
        "daily_pnl_by_signal_date_utc": dict(sorted(daily.items())),
        "actual_redemption_timing_validated": False,
        "settlement_cash_proxy": "retrospective Gamma closedTime; local receipt/spendability clock absent",
        "formal_oos_result": None,
    }
    return metrics, rows, ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-bundle", type=Path, required=True)
    parser.add_argument("--test-bundle", type=Path, required=True)
    parser.add_argument("--train-archive", type=Path, required=True)
    parser.add_argument("--test-archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    registration = {
        "version": VERSION, "candidate_id": CANDIDATE_ID,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "historical_chronological_sensitivity_not_formal_oos",
        "train_archive_sha256": digest(args.train_archive),
        "test_archive_sha256": digest(args.test_archive),
        "code_sha256": digest(Path(__file__)),
        "fit": {"model_id": "matched_market", "parameters": dict(trees.FIXED_PARAMETERS, max_leaf_nodes=8)},
        "entry_margin": MARGIN, "decision_grid_ms": GRID_MS,
        "max_quote_age_ms": MAX_QUOTE_AGE_MS, "latencies_ms": list(LATENCIES_MS),
        "requested_shares": REQUESTED_SHARES, "initial_cash_usd": INITIAL_CASH_USD,
        "fee_rate": FEE_RATE, "limit": "decision ask", "tif": "FAK", "retry": False,
        "legacy_down_proxy": "ask=1-Up bid; size=Up bid size",
        "known_limitations": [
            "Both recordings are historical and the family was selected later; this is not untouched OOS.",
            "Gamma outcome/closedTime are retrospective and have no recorded local receipt/effective clock.",
            "Only the Up top level is clocked; Down and depth beyond top are unavailable.",
            "Fee/tick/min-order metadata are retrospective, not proven effective as of each decision.",
            "No own-order market impact or queue competition can be recovered from this tape.",
        ],
        "formal_oos_result": None,
    }
    write_json(args.out / "registration.json", registration)  # written before fitting/scoring

    train_tape, train_gamma, train_audit = load_tape(args.train_bundle)
    train_grid = decision_grid(train_tape)
    model, model_audit = fit_model(train_grid, train_gamma)
    del train_tape, train_grid
    test_tape, test_gamma, test_audit = load_tape(args.test_bundle)
    if max(x["gamma_closed_ms"] for x in train_gamma.values() if x["winner"] is not None) >= int(test_tape["recv_ms"].min()):
        raise ValueError("training resolution proxy is not strictly earlier than test recording")
    test_grid = decision_grid(test_tape)
    all_signals = score_grid(model, test_grid)
    resolved_test = {m for m, value in test_gamma.items() if value["winner"] is not None}
    signals = all_signals.loc[all_signals["market_id"].isin(resolved_test)].reset_index(drop=True)
    signals["winner"] = [test_gamma[m]["winner"] for m in signals["market_id"]]
    all_metrics = []
    attempts_by_latency = {}
    for latency in LATENCIES_MS:
        potential = potential_attempts(signals, test_tape, test_gamma, latency)
        metrics, attempts, ledger = cash_replay(potential)
        all_metrics.append(metrics)
        attempts_by_latency[latency] = attempts
        with gzip.open(args.out / f"attempts_{latency}ms.jsonl.gz", "wt") as handle:
            for row in attempts:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        with gzip.open(args.out / f"cash_ledger_{latency}ms.jsonl.gz", "wt") as handle:
            for row in ledger:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    write_json(args.out / "model_audit.json", model_audit)
    write_json(args.out / "input_audit.json", {
        "train": train_audit, "test": test_audit,
        "train_latest_gamma_closed_ms": max(x["gamma_closed_ms"] for x in train_gamma.values()
                                             if x["winner"] is not None),
        "test_first_receive_ms": int(test_tape["recv_ms"].min()),
        "strict_chronology_pass": True,
        "test_grid_rows": len(test_grid), "test_signal_markets_all": len(all_signals),
        "test_signal_markets_with_retrospective_label": len(signals),
        "test_unresolved_markets_excluded_from_pnl": len(test_gamma) - len(resolved_test),
    })
    diagnostics = {
        "signal_direction_correct": int((signals["side"] == signals["winner"]).sum()),
        "signal_direction_total": len(signals),
        "fill_diagnostics": {},
        "cross_latency": [],
    }
    for latency, attempts in attempts_by_latency.items():
        fills = [row for row in attempts if row["filled_shares"] > 0]
        wins = [row for row in fills if row["side"] == row["winner"]]
        losses = [row for row in fills if row["side"] != row["winner"]]
        diagnostics["fill_diagnostics"][str(latency)] = {
            "filled_orders": len(fills), "winning_orders": len(wins), "losing_orders": len(losses),
            "winning_shares": math.fsum(row["filled_shares"] for row in wins),
            "losing_shares": math.fsum(row["filled_shares"] for row in losses),
            "pnl_recomputed_usd": math.fsum(row["net_pnl_usd"] for row in fills),
        }
    for left, right in ((500, 600), (600, 1000), (500, 1000)):
        a = {row["market_id"]: row for row in attempts_by_latency[left]}
        b = {row["market_id"]: row for row in attempts_by_latency[right]}
        filled_a = {m for m, row in a.items() if row["filled_shares"] > 0}
        filled_b = {m for m, row in b.items() if row["filled_shares"] > 0}
        deltas = sorted(({
            "market_id": market, "pnl_delta_right_minus_left_usd": b[market]["net_pnl_usd"] - a[market]["net_pnl_usd"],
            "left_status": a[market]["status"], "right_status": b[market]["status"],
            "side": a[market]["side"], "winner": a[market]["winner"],
        } for market in a), key=lambda row: abs(row["pnl_delta_right_minus_left_usd"]), reverse=True)
        diagnostics["cross_latency"].append({
            "left_ms": left, "right_ms": right,
            "filled_both": len(filled_a & filled_b), "filled_left_only": len(filled_a - filled_b),
            "filled_right_only": len(filled_b - filled_a),
            "pnl_delta_right_minus_left_usd": math.fsum(row["pnl_delta_right_minus_left_usd"] for row in deltas),
            "largest_absolute_market_deltas": deltas[:10],
        })
    signals.to_csv(args.out / "first_signals.csv", index=False)
    write_json(args.out / "diagnostics.json", diagnostics)
    write_json(args.out / "metrics.json", all_metrics)
    write_json(args.out / "completion.json", {
        "candidate_id": CANDIDATE_ID, "completed_latency_scenarios": len(all_metrics),
        "test_markets": len(test_gamma), "test_markets_with_retrospective_label": len(resolved_test),
        "signal_markets_all": len(all_signals), "signal_markets_in_pnl": len(signals),
        "formal_oos_result": None, "confirmed_strategies": 0,
    })
    print(json.dumps({"candidate": CANDIDATE_ID, "signals": len(signals), "metrics": all_metrics}, allow_nan=False))


if __name__ == "__main__":
    main()
