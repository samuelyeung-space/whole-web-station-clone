"""Receipt-causal two-leg FAK inventory and shared-cash replay.

This module is deliberately strategy-agnostic.  It accepts already-frozen order
intents and evaluates only execution, cash and settlement mechanics.  Displayed
depth is an upper bound: no queue or hidden-liquidity inference is made.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Literal


Side = Literal["YES", "NO"]


@dataclass(frozen=True)
class LegIntent:
    order_id: str
    pair_id: str
    market_id: str
    side: Side
    decision_ms: int
    arrival_ms: int
    settlement_ms: int
    cash_release_ms: int
    outcome_yes: bool
    requested_shares: float
    fixed_limit: float
    arrival_ask: float
    displayed_shares: float


@dataclass(frozen=True)
class ReplayConfig:
    initial_cash: float = 1000.0
    depth_haircut: float = 1.0
    fee_rate: float = 0.07
    full_or_zero: bool = False
    extra_cost_per_share: float = 0.0


@dataclass
class Position:
    yes_shares: float = 0.0
    no_shares: float = 0.0
    cash_cost: float = 0.0


@dataclass(frozen=True)
class Fill:
    order_id: str
    pair_id: str
    market_id: str
    side: Side
    arrival_ms: int
    requested_shares: float
    filled_shares: float
    price: float
    fee: float
    extra_cost: float
    cash_debit: float


def taker_fee(shares: float, price: float, fee_rate: float) -> float:
    """Crypto taker-fee model used by the frozen research replays."""
    return shares * fee_rate * price * (1.0 - price)


def _number(value: object, name: str) -> None:
    """Require a finite JSON-number value without coercion.

    The replay input is JSON, so the only valid numeric runtime types are int
    and float.  Reject bool explicitly (it is an int subclass) and reject
    strings rather than interpreting values such as ``"0"`` or ``"false"``.
    """
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite JSON number")


def _identifier(value: object, name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _timestamp(value: object, name: str) -> None:
    # bool is a subclass of int in Python, so use an exact type check.  Loose
    # coercion here can silently reorder events or settle a market at the wrong
    # instant.
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def validate_intent(intent: LegIntent) -> None:
    for name in ("order_id", "pair_id", "market_id"):
        _identifier(getattr(intent, name), name)
    if intent.side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")
    for name in ("decision_ms", "arrival_ms", "settlement_ms", "cash_release_ms"):
        _timestamp(getattr(intent, name), name)
    if type(intent.outcome_yes) is not bool:
        raise ValueError("outcome_yes must be boolean")
    if intent.arrival_ms < intent.decision_ms:
        raise ValueError("arrival_ms precedes decision_ms")
    if intent.settlement_ms <= intent.arrival_ms:
        raise ValueError("settlement_ms must follow arrival_ms")
    if intent.cash_release_ms < intent.settlement_ms:
        raise ValueError("cash_release_ms must not precede settlement_ms")
    for name in (
        "requested_shares",
        "fixed_limit",
        "arrival_ask",
        "displayed_shares",
    ):
        _number(getattr(intent, name), name)
    if intent.requested_shares <= 0 or intent.displayed_shares < 0:
        raise ValueError("share quantities are invalid")
    if not 0 < intent.fixed_limit < 1 or not 0 < intent.arrival_ask < 1:
        raise ValueError("prices must be strictly between zero and one")


def validate_config(config: ReplayConfig) -> None:
    for name in ("initial_cash", "depth_haircut", "fee_rate", "extra_cost_per_share"):
        _number(getattr(config, name), name)
    if type(config.full_or_zero) is not bool:
        raise ValueError("full_or_zero must be boolean")
    if config.initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if not 0 <= config.depth_haircut <= 1:
        raise ValueError("depth_haircut must be in [0, 1]")
    if config.fee_rate < 0 or config.extra_cost_per_share < 0:
        raise ValueError("cost parameters must be non-negative")


def replay(intents: Iterable[LegIntent], config: ReplayConfig = ReplayConfig()) -> dict:
    """Replay frozen FAK legs in receipt order against one cash account.

    Settlement events are processed before order arrivals at the same timestamp.
    The reported drawdown uses conservative terminal value: matched YES/NO shares
    are worth one dollar per pair and unmatched inventory is marked at zero.
    """

    validate_config(config)
    orders = list(intents)
    for order in orders:
        validate_intent(order)
    ids = [order.order_id for order in orders]
    if len(ids) != len(set(ids)):
        raise ValueError("order_id values must be unique")

    # A pair is one YES/NO attempt in one market.  Failing closed here matters:
    # the execution ledger is market-scoped, while the summary below is
    # pair-scoped.  Reusing a pair_id across markets (or for two orders on the
    # same side) would otherwise make unrelated fills look like a completed
    # hedge and corrupt the reported arrival gap/residual inventory.
    pair_legs: dict[str, dict[Side, str]] = {}
    pair_markets: dict[str, str] = {}
    for order in orders:
        prior_market = pair_markets.setdefault(order.pair_id, order.market_id)
        if prior_market != order.market_id:
            raise ValueError("pair_id values must not span markets")
        legs = pair_legs.setdefault(order.pair_id, {})
        if order.side in legs:
            raise ValueError("pair_id values must contain at most one order per side")
        legs[order.side] = order.order_id
    market_pair_ids: dict[str, list[str]] = {}
    for pair_id, market_id in pair_markets.items():
        market_pair_ids.setdefault(market_id, []).append(pair_id)

    market_meta: dict[str, tuple[int, int, bool]] = {}
    for order in orders:
        meta = (order.settlement_ms, order.cash_release_ms, order.outcome_yes)
        prior = market_meta.setdefault(order.market_id, meta)
        if prior != meta:
            raise ValueError("inconsistent settlement metadata within market")

    events: list[tuple[int, int, str, object]] = []
    for market_id, (settlement_ms, cash_release_ms, outcome_yes) in market_meta.items():
        events.append((settlement_ms, 0, market_id, outcome_yes))
        events.append((cash_release_ms, 1, market_id, outcome_yes))
    for order in orders:
        events.append((order.arrival_ms, 2, order.order_id, order))
    events.sort(key=lambda row: (row[0], row[1], row[2]))

    cash = config.initial_cash
    positions: dict[str, Position] = {}
    pair_positions: dict[str, Position] = {}
    pending_payouts: dict[str, float] = {}
    fills: list[Fill] = []
    ledger: list[dict] = []
    counts = {
        "orders": len(orders),
        "full_fills": 0,
        "partial_fills": 0,
        "zero_fills": 0,
        "price_rejects": 0,
        "cash_rejects": 0,
    }
    max_gross_exposure = 0.0
    max_unhedged_shares = 0.0
    unhedged_episode_count = 0
    total_unhedged_ms = 0
    max_unhedged_episode_ms = 0
    market_last_ms: dict[str, int] = {}
    market_unhedged: dict[str, bool] = {}
    market_episode_start: dict[str, int] = {}
    max_pair_unhedged_shares = 0.0
    pair_unhedged_episode_count = 0
    total_pair_unhedged_ms = 0
    max_pair_unhedged_episode_ms = 0
    pair_last_ms: dict[str, int] = {}
    pair_unhedged: dict[str, bool] = {}
    pair_episode_start: dict[str, int] = {}
    peak_conservative_equity = config.initial_cash
    max_conservative_drawdown = 0.0
    cash_min = cash
    max_pending_payout = 0.0

    def position_snapshot(market_id: str) -> dict[str, float]:
        position = positions.get(market_id, Position())
        return {
            "yes_shares": position.yes_shares,
            "no_shares": position.no_shares,
            "cash_cost": position.cash_cost,
        }

    def advance_exposure_clock(market_id: str, timestamp_ms: int) -> None:
        nonlocal total_unhedged_ms
        prior_ms = market_last_ms.setdefault(market_id, timestamp_ms)
        if timestamp_ms < prior_ms:
            raise RuntimeError("internal error: market event clock regressed")
        if market_unhedged.get(market_id, False):
            total_unhedged_ms += timestamp_ms - prior_ms
        market_last_ms[market_id] = timestamp_ms

    def refresh_exposure_state(market_id: str, timestamp_ms: int) -> None:
        nonlocal unhedged_episode_count, max_unhedged_episode_ms
        position = positions.get(market_id, Position())
        now_unhedged = abs(position.yes_shares - position.no_shares) > 1e-12
        was_unhedged = market_unhedged.get(market_id, False)
        if now_unhedged and not was_unhedged:
            unhedged_episode_count += 1
            market_episode_start[market_id] = timestamp_ms
        elif was_unhedged and not now_unhedged:
            started = market_episode_start.pop(market_id)
            max_unhedged_episode_ms = max(
                max_unhedged_episode_ms, timestamp_ms - started
            )
        market_unhedged[market_id] = now_unhedged

    def advance_pair_exposure_clock(pair_id: str, timestamp_ms: int) -> None:
        nonlocal total_pair_unhedged_ms
        prior_ms = pair_last_ms.setdefault(pair_id, timestamp_ms)
        if timestamp_ms < prior_ms:
            raise RuntimeError("internal error: pair event clock regressed")
        if pair_unhedged.get(pair_id, False):
            total_pair_unhedged_ms += timestamp_ms - prior_ms
        pair_last_ms[pair_id] = timestamp_ms

    def refresh_pair_exposure_state(pair_id: str, timestamp_ms: int) -> None:
        nonlocal pair_unhedged_episode_count, max_pair_unhedged_episode_ms
        position = pair_positions.get(pair_id, Position())
        now_unhedged = abs(position.yes_shares - position.no_shares) > 1e-12
        was_unhedged = pair_unhedged.get(pair_id, False)
        if now_unhedged and not was_unhedged:
            pair_unhedged_episode_count += 1
            pair_episode_start[pair_id] = timestamp_ms
        elif was_unhedged and not now_unhedged:
            started = pair_episode_start.pop(pair_id)
            max_pair_unhedged_episode_ms = max(
                max_pair_unhedged_episode_ms, timestamp_ms - started
            )
        pair_unhedged[pair_id] = now_unhedged

    def mark_risk() -> None:
        nonlocal max_gross_exposure, max_unhedged_shares, max_pair_unhedged_shares
        nonlocal peak_conservative_equity, max_conservative_drawdown, cash_min
        gross = sum(position.cash_cost for position in positions.values())
        matched_value = sum(
            min(position.yes_shares, position.no_shares)
            for position in positions.values()
        )
        equity = cash + matched_value + sum(pending_payouts.values())
        max_gross_exposure = max(max_gross_exposure, gross)
        cash_min = min(cash_min, cash)
        peak_conservative_equity = max(peak_conservative_equity, equity)
        max_conservative_drawdown = max(
            max_conservative_drawdown, peak_conservative_equity - equity
        )
        for position in positions.values():
            unhedged = abs(position.yes_shares - position.no_shares)
            max_unhedged_shares = max(max_unhedged_shares, unhedged)
        for position in pair_positions.values():
            unhedged = abs(position.yes_shares - position.no_shares)
            max_pair_unhedged_shares = max(max_pair_unhedged_shares, unhedged)

    for timestamp_ms, event_kind, event_id, payload in events:
        if event_kind == 0:
            advance_exposure_clock(event_id, timestamp_ms)
            for pair_id in market_pair_ids[event_id]:
                advance_pair_exposure_clock(pair_id, timestamp_ms)
            before_cash = cash
            before_position = position_snapshot(event_id)
            position = positions.pop(event_id, None)
            payout = 0.0
            if position is not None:
                outcome_yes = bool(payload)
                payout = position.yes_shares if outcome_yes else position.no_shares
            pending_payouts[event_id] = payout
            max_pending_payout = max(
                max_pending_payout, sum(pending_payouts.values())
            )
            for pair_id in market_pair_ids[event_id]:
                pair_positions.pop(pair_id, None)
                refresh_pair_exposure_state(pair_id, timestamp_ms)
            refresh_exposure_state(event_id, timestamp_ms)
            mark_risk()
            ledger.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "event": "settlement",
                    "market_id": event_id,
                    "outcome_yes": bool(payload),
                    "cash_before": before_cash,
                    "cash_after": cash,
                    "payout_locked": payout,
                    "position_before": before_position,
                    "position_after": position_snapshot(event_id),
                }
            )
            continue

        if event_kind == 1:
            before_cash = cash
            payout = pending_payouts.pop(event_id)
            cash += payout
            mark_risk()
            ledger.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "event": "cash_release",
                    "market_id": event_id,
                    "cash_before": before_cash,
                    "cash_after": cash,
                    "payout_released": payout,
                    "settlement_ms": market_meta[event_id][0],
                    "release_delay_ms": timestamp_ms - market_meta[event_id][0],
                }
            )
            continue

        order = payload
        assert isinstance(order, LegIntent)
        advance_exposure_clock(order.market_id, timestamp_ms)
        advance_pair_exposure_clock(order.pair_id, timestamp_ms)
        before_cash = cash
        before_position = position_snapshot(order.market_id)
        status = "zero_fill"
        reason = "no_displayed_depth"
        shares = 0.0
        fee = 0.0
        extra_cost = 0.0
        debit = 0.0
        if order.arrival_ask > order.fixed_limit + 1e-12:
            counts["price_rejects"] += 1
            counts["zero_fills"] += 1
            reason = "arrival_ask_above_fixed_limit"
        else:
            available = order.displayed_shares * config.depth_haircut
            shares = min(order.requested_shares, available)
            if config.full_or_zero and shares + 1e-12 < order.requested_shares:
                shares = 0.0
                reason = "full_or_zero_depth_shortfall"
            if shares <= 1e-12:
                counts["zero_fills"] += 1
            else:
                fee = taker_fee(shares, order.arrival_ask, config.fee_rate)
                extra_cost = shares * config.extra_cost_per_share
                debit = shares * order.arrival_ask + fee + extra_cost
                if debit > cash + 1e-12:
                    counts["cash_rejects"] += 1
                    counts["zero_fills"] += 1
                    reason = "insufficient_shared_cash"
                    shares = 0.0
                    fee = 0.0
                    extra_cost = 0.0
                    debit = 0.0
                else:
                    cash -= debit
                    position = positions.setdefault(order.market_id, Position())
                    if order.side == "YES":
                        position.yes_shares += shares
                    else:
                        position.no_shares += shares
                    position.cash_cost += debit
                    pair_position = pair_positions.setdefault(order.pair_id, Position())
                    if order.side == "YES":
                        pair_position.yes_shares += shares
                    else:
                        pair_position.no_shares += shares
                    pair_position.cash_cost += debit
                    fill = Fill(
                        order_id=order.order_id,
                        pair_id=order.pair_id,
                        market_id=order.market_id,
                        side=order.side,
                        arrival_ms=order.arrival_ms,
                        requested_shares=order.requested_shares,
                        filled_shares=shares,
                        price=order.arrival_ask,
                        fee=fee,
                        extra_cost=extra_cost,
                        cash_debit=debit,
                    )
                    fills.append(fill)
                    if shares + 1e-12 >= order.requested_shares:
                        counts["full_fills"] += 1
                        status = "full_fill"
                    else:
                        counts["partial_fills"] += 1
                        status = "partial_fill"
                    reason = "executed"
        refresh_exposure_state(order.market_id, timestamp_ms)
        refresh_pair_exposure_state(order.pair_id, timestamp_ms)
        mark_risk()
        ledger.append(
            {
                "timestamp_ms": timestamp_ms,
                "event": "order_arrival",
                "order_id": order.order_id,
                "pair_id": order.pair_id,
                "market_id": order.market_id,
                "side": order.side,
                "status": status,
                "reason": reason,
                "requested_shares": order.requested_shares,
                "filled_shares": shares,
                "cash_before": before_cash,
                "cash_after": cash,
                "cash_debit": debit,
                "fee": fee,
                "extra_cost": extra_cost,
                "position_before": before_position,
                "position_after": position_snapshot(order.market_id),
            }
        )

    if positions:
        raise RuntimeError("internal error: unsettled positions remain")
    if pair_positions:
        raise RuntimeError("internal error: unsettled pair positions remain")
    if pending_payouts:
        raise RuntimeError("internal error: unreleased payouts remain")
    pair_stats: dict[str, dict] = {}
    for fill in fills:
        stats = pair_stats.setdefault(
            fill.pair_id,
            {"yes_shares": 0.0, "no_shares": 0.0, "arrivals_ms": []},
        )
        stats["yes_shares" if fill.side == "YES" else "no_shares"] += fill.filled_shares
        stats["arrivals_ms"].append(fill.arrival_ms)
    paired_stats = [
        stats
        for stats in pair_stats.values()
        if stats["yes_shares"] > 1e-12 and stats["no_shares"] > 1e-12
    ]
    paired = len(paired_stats)
    single_leg = sum(
        (stats["yes_shares"] > 1e-12) != (stats["no_shares"] > 1e-12)
        for stats in pair_stats.values()
    )
    residual_pairs = sum(
        abs(stats["yes_shares"] - stats["no_shares"]) > 1e-12
        for stats in paired_stats
    )
    max_pair_arrival_gap_ms = max(
        (max(stats["arrivals_ms"]) - min(stats["arrivals_ms"]) for stats in paired_stats),
        default=0,
    )
    result = {
        "schema_version": 4,
        "config": asdict(config),
        "counts": {
            **counts,
            "fills": len(fills),
            "pairs_with_both_sides_filled": paired,
            "pairs_with_single_side_filled": single_leg,
            "pairs_with_residual_inventory": residual_pairs,
            "unhedged_market_episodes": unhedged_episode_count,
            "unhedged_pair_episodes": pair_unhedged_episode_count,
        },
        "cash": {
            "initial": config.initial_cash,
            "minimum": cash_min,
            "final": cash,
            "net_pnl": cash - config.initial_cash,
            "max_pending_payout": max_pending_payout,
        },
        "risk": {
            "max_gross_cash_exposure": max_gross_exposure,
            "max_unhedged_shares": max_unhedged_shares,
            "total_unhedged_ms": total_unhedged_ms,
            "max_unhedged_episode_ms": max_unhedged_episode_ms,
            "max_pair_unhedged_shares": max_pair_unhedged_shares,
            "total_pair_unhedged_ms": total_pair_unhedged_ms,
            "max_pair_unhedged_episode_ms": max_pair_unhedged_episode_ms,
            "max_pair_arrival_gap_ms": max_pair_arrival_gap_ms,
            "max_conservative_drawdown": max_conservative_drawdown,
            "total_cash_release_delay_ms": sum(
                cash_release_ms - settlement_ms
                for settlement_ms, cash_release_ms, _ in market_meta.values()
            ),
            "max_cash_release_delay_ms": max(
                (
                    cash_release_ms - settlement_ms
                    for settlement_ms, cash_release_ms, _ in market_meta.values()
                ),
                default=0,
            ),
        },
        "fills": [asdict(fill) for fill in fills],
        "ledger": ledger,
        "limitations": [
            "displayed depth is only a fill upper bound",
            "no queue position, hidden liquidity or market impact is inferred",
            "unmatched inventory is marked at zero for conservative drawdown",
            "market-net and pair-local unhedged exposure are reported separately",
            "cash_release_ms must be observed or explicitly stressed; market settlement does not imply immediate spendability",
        ],
    }
    return result


def _load(path: Path) -> tuple[list[LegIntent], ReplayConfig]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("intents"), list):
        raise ValueError("input must be an object with an intents array")
    config = ReplayConfig(**payload.get("config", {}))
    return [LegIntent(**row) for row in payload["intents"]], config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    intents, config = _load(args.input)
    rendered = json.dumps(replay(intents, config), indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

