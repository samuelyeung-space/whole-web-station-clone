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


def _finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def validate_intent(intent: LegIntent) -> None:
    if not intent.order_id or not intent.pair_id or not intent.market_id:
        raise ValueError("order_id, pair_id and market_id are required")
    if intent.side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")
    if intent.arrival_ms < intent.decision_ms:
        raise ValueError("arrival_ms precedes decision_ms")
    if intent.settlement_ms < intent.arrival_ms:
        raise ValueError("settlement_ms precedes arrival_ms")
    for name in (
        "requested_shares",
        "fixed_limit",
        "arrival_ask",
        "displayed_shares",
    ):
        _finite(float(getattr(intent, name)), name)
    if intent.requested_shares <= 0 or intent.displayed_shares < 0:
        raise ValueError("share quantities are invalid")
    if not 0 < intent.fixed_limit < 1 or not 0 < intent.arrival_ask < 1:
        raise ValueError("prices must be strictly between zero and one")


def validate_config(config: ReplayConfig) -> None:
    for name in ("initial_cash", "depth_haircut", "fee_rate", "extra_cost_per_share"):
        _finite(float(getattr(config, name)), name)
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

    market_meta: dict[str, tuple[int, bool]] = {}
    for order in orders:
        meta = (order.settlement_ms, order.outcome_yes)
        prior = market_meta.setdefault(order.market_id, meta)
        if prior != meta:
            raise ValueError("inconsistent settlement metadata within market")

    events: list[tuple[int, int, str, object]] = []
    for market_id, (settlement_ms, outcome_yes) in market_meta.items():
        events.append((settlement_ms, 0, market_id, outcome_yes))
    for order in orders:
        events.append((order.arrival_ms, 1, order.order_id, order))
    events.sort(key=lambda row: (row[0], row[1], row[2]))

    cash = config.initial_cash
    positions: dict[str, Position] = {}
    fills: list[Fill] = []
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
    inventory_episodes: set[tuple[str, str]] = set()
    peak_conservative_equity = config.initial_cash
    max_conservative_drawdown = 0.0
    cash_min = cash

    def mark_risk() -> None:
        nonlocal max_gross_exposure, max_unhedged_shares
        nonlocal peak_conservative_equity, max_conservative_drawdown, cash_min
        gross = sum(position.cash_cost for position in positions.values())
        matched_value = sum(
            min(position.yes_shares, position.no_shares)
            for position in positions.values()
        )
        equity = cash + matched_value
        max_gross_exposure = max(max_gross_exposure, gross)
        cash_min = min(cash_min, cash)
        peak_conservative_equity = max(peak_conservative_equity, equity)
        max_conservative_drawdown = max(
            max_conservative_drawdown, peak_conservative_equity - equity
        )
        for market_id, position in positions.items():
            unhedged = abs(position.yes_shares - position.no_shares)
            max_unhedged_shares = max(max_unhedged_shares, unhedged)
            if unhedged > 1e-12:
                inventory_episodes.add((market_id, "unhedged"))

    for _, event_kind, event_id, payload in events:
        if event_kind == 0:
            position = positions.pop(event_id, None)
            if position is not None:
                outcome_yes = bool(payload)
                payout = position.yes_shares if outcome_yes else position.no_shares
                cash += payout
            mark_risk()
            continue

        order = payload
        assert isinstance(order, LegIntent)
        if order.arrival_ask > order.fixed_limit + 1e-12:
            counts["price_rejects"] += 1
            counts["zero_fills"] += 1
            mark_risk()
            continue
        available = order.displayed_shares * config.depth_haircut
        shares = min(order.requested_shares, available)
        if config.full_or_zero and shares + 1e-12 < order.requested_shares:
            shares = 0.0
        if shares <= 1e-12:
            counts["zero_fills"] += 1
            mark_risk()
            continue
        fee = taker_fee(shares, order.arrival_ask, config.fee_rate)
        extra_cost = shares * config.extra_cost_per_share
        debit = shares * order.arrival_ask + fee + extra_cost
        if debit > cash + 1e-12:
            counts["cash_rejects"] += 1
            counts["zero_fills"] += 1
            mark_risk()
            continue
        cash -= debit
        position = positions.setdefault(order.market_id, Position())
        if order.side == "YES":
            position.yes_shares += shares
        else:
            position.no_shares += shares
        position.cash_cost += debit
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
        else:
            counts["partial_fills"] += 1
        mark_risk()

    if positions:
        raise RuntimeError("internal error: unsettled positions remain")
    pair_sides: dict[str, set[str]] = {}
    for fill in fills:
        pair_sides.setdefault(fill.pair_id, set()).add(fill.side)
    paired = sum(sides == {"YES", "NO"} for sides in pair_sides.values())
    single_leg = sum(len(sides) == 1 for sides in pair_sides.values())
    result = {
        "schema_version": 1,
        "config": asdict(config),
        "counts": {
            **counts,
            "fills": len(fills),
            "pairs_with_both_sides_filled": paired,
            "pairs_with_single_side_filled": single_leg,
            "unhedged_market_episodes": len(inventory_episodes),
        },
        "cash": {
            "initial": config.initial_cash,
            "minimum": cash_min,
            "final": cash,
            "net_pnl": cash - config.initial_cash,
        },
        "risk": {
            "max_gross_cash_exposure": max_gross_exposure,
            "max_unhedged_shares": max_unhedged_shares,
            "max_conservative_drawdown": max_conservative_drawdown,
        },
        "fills": [asdict(fill) for fill in fills],
        "limitations": [
            "displayed depth is only a fill upper bound",
            "no queue position, hidden liquidity or market impact is inferred",
            "unmatched inventory is marked at zero for conservative drawdown",
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
