"""Receipt-causal two-leg FAK inventory and shared-cash replay.

This module is deliberately strategy-agnostic.  It accepts already-frozen order
intents and evaluates only execution, cash and settlement mechanics.  Displayed
depth is an upper bound: no queue or hidden-liquidity inference is made.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
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
    send_ms: int | None = None
    snapshot_id: str | None = None
    ack_ms: int | None = None


@dataclass(frozen=True)
class ReplayConfig:
    initial_cash: float = 1000.0
    depth_haircut: float = 1.0
    fee_rate: float = 0.07
    full_or_zero: bool = False
    extra_cost_per_share: float = 0.0
    reserve_cash_at_send: bool = False
    consume_snapshot_depth: bool = False
    reserve_until_ack: bool = False


@dataclass(frozen=True)
class MarketFee:
    """Caller-supplied historical fee metadata; references are not authenticated.

    One record covers one market for the entire replay. Half-open effective
    intervals must cover decision through match, and the local observation must
    strictly precede decision (equal-clock ordering is not inferred).
    """

    market_id: str
    fee_rate: float
    effective_from_ms: int
    effective_until_ms: int
    observed_ms: int
    source_ref: str
    evidence_sha256: str


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
    """Crypto taker fee rounded per order to five decimal places.

    The frozen strategy replays use decimal half-up rounding rather than an
    unrounded binary float or Python's ties-to-even ``round``.  Fee rounding is
    applied to the aggregate order fee, not independently to every share.
    """
    raw = (
        Decimal(str(shares))
        * Decimal(str(fee_rate))
        * Decimal(str(price))
        * (Decimal("1") - Decimal(str(price)))
    )
    return float(raw.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))


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
    if intent.send_ms is not None:
        _timestamp(intent.send_ms, "send_ms")
        if intent.send_ms < intent.decision_ms:
            raise ValueError("send_ms precedes decision_ms")
        if intent.arrival_ms < intent.send_ms:
            raise ValueError("arrival_ms precedes send_ms")
    if intent.snapshot_id is not None:
        _identifier(intent.snapshot_id, "snapshot_id")
    if intent.ack_ms is not None:
        _timestamp(intent.ack_ms, "ack_ms")
        if intent.ack_ms < intent.arrival_ms:
            raise ValueError("ack_ms precedes arrival_ms")
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
    if type(config.reserve_cash_at_send) is not bool:
        raise ValueError("reserve_cash_at_send must be boolean")
    if type(config.consume_snapshot_depth) is not bool:
        raise ValueError("consume_snapshot_depth must be boolean")
    if type(config.reserve_until_ack) is not bool:
        raise ValueError("reserve_until_ack must be boolean")
    if config.reserve_until_ack and not config.reserve_cash_at_send:
        raise ValueError("reserve_until_ack requires reserve_cash_at_send")
    if config.initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if not 0 <= config.depth_haircut <= 1:
        raise ValueError("depth_haircut must be in [0, 1]")
    if config.fee_rate < 0 or config.extra_cost_per_share < 0:
        raise ValueError("cost parameters must be non-negative")


def _market_fee_records(
    records: Iterable[MarketFee], orders: list[LegIntent]
) -> dict[str, MarketFee]:
    by_market: dict[str, MarketFee] = {}
    for record in records:
        if type(record) is not MarketFee:
            raise ValueError("market_fees must contain MarketFee records")
        _identifier(record.market_id, "fee market_id")
        _identifier(record.source_ref, "fee source_ref")
        if not record.source_ref.strip():
            raise ValueError("fee source_ref must not be whitespace")
        if type(record.evidence_sha256) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", record.evidence_sha256
        ):
            raise ValueError("fee evidence_sha256 must be a lowercase SHA256")
        _number(record.fee_rate, "market fee_rate")
        if not 0 <= record.fee_rate <= 1:
            raise ValueError("market fee_rate must be in [0, 1]")
        for name in ("effective_from_ms", "effective_until_ms", "observed_ms"):
            _timestamp(getattr(record, name), "fee " + name)
        if record.effective_until_ms <= record.effective_from_ms:
            raise ValueError("fee effective interval must be non-empty")
        if record.market_id in by_market:
            raise ValueError("one fee record per market is required")
        by_market[record.market_id] = record
    if set(by_market) != {order.market_id for order in orders}:
        raise ValueError("fee metadata must exactly cover replay markets")
    for order in orders:
        record = by_market[order.market_id]
        if record.observed_ms >= order.decision_ms:
            raise ValueError("fee observation must strictly precede decision")
        if not (record.effective_from_ms <= order.decision_ms
                and order.arrival_ms < record.effective_until_ms):
            raise ValueError("fee interval must cover decision through arrival")
    return by_market


def replay(
    intents: Iterable[LegIntent], config: ReplayConfig = ReplayConfig(), *,
    market_fees: Iterable[MarketFee] | None = None,
) -> dict:
    """Replay frozen FAK legs in receipt order against one cash account.

    Settlement events are processed before order arrivals at the same timestamp.
    The reported drawdown uses conservative terminal value: matched YES/NO shares
    are worth one dollar per pair and unmatched inventory is marked at zero.
    """

    validate_config(config)
    orders = list(intents)
    for order in orders:
        validate_intent(order)
        if config.reserve_cash_at_send and order.send_ms is None:
            raise ValueError("send_ms is required when reserve_cash_at_send is enabled")
        if config.reserve_until_ack and order.ack_ms is None:
            raise ValueError("ack_ms is required when reserve_until_ack is enabled")
        if config.consume_snapshot_depth and order.snapshot_id is None:
            raise ValueError(
                "snapshot_id is required when consume_snapshot_depth is enabled"
            )
    ids = [order.order_id for order in orders]
    if len(ids) != len(set(ids)):
        raise ValueError("order_id values must be unique")
    fees = _market_fee_records(market_fees, orders) if market_fees is not None else None

    def order_fee_rate(order: LegIntent) -> float:
        return fees[order.market_id].fee_rate if fees is not None else config.fee_rate

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

    # Multiple intents can be evaluated against one recorded top-of-book
    # snapshot.  Treating the displayed size as fresh for every order would
    # let concurrent strategies consume the same liquidity repeatedly.  The
    # opt-in qualification mode binds each quote to an exact snapshot id and
    # shares one depth pool per market/token/snapshot.
    snapshot_depth: dict[tuple[str, Side, str], dict[str, float]] = {}
    if config.consume_snapshot_depth:
        for order in orders:
            assert order.snapshot_id is not None
            key = (order.market_id, order.side, order.snapshot_id)
            quote = {
                "arrival_ask": order.arrival_ask,
                "displayed_shares": order.displayed_shares,
                "initial": order.displayed_shares * config.depth_haircut,
                "remaining": order.displayed_shares * config.depth_haircut,
            }
            prior = snapshot_depth.setdefault(key, quote)
            if (
                prior["arrival_ask"] != quote["arrival_ask"]
                or prior["displayed_shares"] != quote["displayed_shares"]
            ):
                raise ValueError("inconsistent quote metadata within snapshot")

    events: list[tuple[int, int, str, object]] = []
    for market_id, (settlement_ms, cash_release_ms, outcome_yes) in market_meta.items():
        events.append((settlement_ms, 0, market_id, outcome_yes))
        events.append((cash_release_ms, 1, market_id, outcome_yes))
    for order in orders:
        if config.reserve_cash_at_send:
            assert order.send_ms is not None
            events.append((order.send_ms, 2, order.order_id, order))
        events.append((order.arrival_ms, 3, order.order_id, order))
        if config.reserve_until_ack:
            assert order.ack_ms is not None
            # Acknowledgements follow all sends/arrivals at an equal clock.
            # This is conservative when sub-millisecond ordering is unknown.
            events.append((order.ack_ms, 4, order.order_id, order))
    events.sort(key=lambda row: (row[0], row[1], row[2]))

    cash = config.initial_cash
    positions: dict[str, Position] = {}
    pair_positions: dict[str, Position] = {}
    pending_payouts: dict[str, float] = {}
    reservations: dict[str, float] = {}
    send_rejected: set[str] = set()
    awaiting_ack: set[str] = set()
    max_pending_ack_cash = 0.0
    max_pending_ack_orders = 0
    ack_count = 0
    fills: list[Fill] = []
    ledger: list[dict] = []
    counts = {
        "orders": len(orders),
        "full_fills": 0,
        "partial_fills": 0,
        "zero_fills": 0,
        "price_rejects": 0,
        "cash_rejects": 0,
        "send_cash_rejects": 0,
        "arrival_cash_rejects": 0,
        "snapshot_depth_rejects": 0,
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
    available_cash_min = cash
    max_cash_reserved = 0.0
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
        nonlocal available_cash_min, max_cash_reserved
        gross = sum(position.cash_cost for position in positions.values())
        matched_value = sum(
            min(position.yes_shares, position.no_shares)
            for position in positions.values()
        )
        equity = cash + matched_value + sum(pending_payouts.values())
        max_gross_exposure = max(max_gross_exposure, gross)
        cash_min = min(cash_min, cash)
        reserved = sum(reservations.values())
        if reserved > cash + 1e-9:
            raise RuntimeError("internal error: cash reservations exceed cash")
        available_cash_min = min(available_cash_min, cash - reserved)
        max_cash_reserved = max(max_cash_reserved, reserved)
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
        if event_kind == 4:
            if order.order_id in send_rejected:
                continue  # No venue order was sent, so there is no venue ack.
            if order.order_id not in awaiting_ack:
                raise RuntimeError("internal error: ack without matched attempt")
            before_reserved = sum(reservations.values())
            released = reservations.pop(order.order_id, 0.0)
            awaiting_ack.remove(order.order_id)
            ack_count += 1
            mark_risk()
            ledger.append({
                "timestamp_ms": timestamp_ms,
                "event": "order_ack",
                "order_id": order.order_id,
                "pair_id": order.pair_id,
                "market_id": order.market_id,
                "cash": cash,
                "reserved_before": before_reserved,
                "reserved_after": sum(reservations.values()),
                "reservation_released": released,
                "available_after": cash - sum(reservations.values()),
                "arrival_to_ack_ms": timestamp_ms - order.arrival_ms,
            })
            continue
        if event_kind == 2:
            before_reserved = sum(reservations.values())
            available_before = cash - before_reserved
            # Reserve a fail-closed upper bound.  The fee curve p*(1-p) is at
            # most 0.25 for every executable price.  Use the same per-order
            # five-decimal rounding as execution so a half-up quantum can
            # never make the actual debit exceed its reservation.
            maximum_fee = taker_fee(
                order.requested_shares, 0.5, order_fee_rate(order)
            )
            reserve = (
                order.requested_shares
                * (order.fixed_limit + config.extra_cost_per_share)
                + maximum_fee
            )
            status = "reserved"
            reason = "worst_case_cash_reserved"
            if reserve > available_before + 1e-12:
                reserve = 0.0
                send_rejected.add(order.order_id)
                counts["cash_rejects"] += 1
                counts["send_cash_rejects"] += 1
                status = "rejected"
                reason = "insufficient_shared_cash_at_send"
            else:
                reservations[order.order_id] = reserve
            mark_risk()
            ledger.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "event": "order_send",
                    "order_id": order.order_id,
                    "pair_id": order.pair_id,
                    "market_id": order.market_id,
                    "side": order.side,
                    "status": status,
                    "reason": reason,
                    "cash": cash,
                    "reserved_before": before_reserved,
                    "reserved_after": sum(reservations.values()),
                    "available_before": available_before,
                    "available_after": cash - sum(reservations.values()),
                    "reservation": reserve,
                }
            )
            continue

        advance_exposure_clock(order.market_id, timestamp_ms)
        advance_pair_exposure_clock(order.pair_id, timestamp_ms)
        before_cash = cash
        before_reserved = sum(reservations.values())
        own_reservation = reservations.pop(order.order_id, 0.0)
        before_position = position_snapshot(order.market_id)
        status = "zero_fill"
        reason = "no_displayed_depth"
        shares = 0.0
        fee = 0.0
        extra_cost = 0.0
        debit = 0.0
        depth_key: tuple[str, Side, str] | None = None
        snapshot_depth_before: float | None = None
        snapshot_depth_after: float | None = None
        if order.order_id in send_rejected:
            counts["zero_fills"] += 1
            reason = "insufficient_shared_cash_at_send"
        elif order.arrival_ask > order.fixed_limit + 1e-12:
            counts["price_rejects"] += 1
            counts["zero_fills"] += 1
            reason = "arrival_ask_above_fixed_limit"
        else:
            if config.consume_snapshot_depth:
                assert order.snapshot_id is not None
                depth_key = (order.market_id, order.side, order.snapshot_id)
                snapshot_depth_before = snapshot_depth[depth_key]["remaining"]
                available = snapshot_depth_before
            else:
                available = order.displayed_shares * config.depth_haircut
            shares = min(order.requested_shares, available)
            if config.full_or_zero and shares + 1e-12 < order.requested_shares:
                shares = 0.0
                reason = "full_or_zero_depth_shortfall"
            if shares <= 1e-12:
                counts["zero_fills"] += 1
                if config.consume_snapshot_depth and available <= 1e-12:
                    counts["snapshot_depth_rejects"] += 1
                    reason = "shared_snapshot_depth_exhausted"
            else:
                fee = taker_fee(shares, order.arrival_ask, order_fee_rate(order))
                extra_cost = shares * config.extra_cost_per_share
                debit = shares * order.arrival_ask + fee + extra_cost
                if debit > cash + 1e-12:
                    counts["cash_rejects"] += 1
                    counts["arrival_cash_rejects"] += 1
                    counts["zero_fills"] += 1
                    reason = "insufficient_shared_cash"
                    shares = 0.0
                    fee = 0.0
                    extra_cost = 0.0
                    debit = 0.0
                else:
                    if config.reserve_cash_at_send:
                        if debit > own_reservation + 1e-9:
                            raise RuntimeError(
                                "internal error: actual debit exceeds reservation"
                            )
                        available_after_other_reservations = cash - sum(
                            reservations.values()
                        )
                        if debit > available_after_other_reservations + 1e-9:
                            raise RuntimeError(
                                "internal error: fill spends another order's reservation"
                            )
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
                    if depth_key is not None:
                        snapshot_depth[depth_key]["remaining"] -= shares
                        if snapshot_depth[depth_key]["remaining"] < -1e-9:
                            raise RuntimeError(
                                "internal error: snapshot depth over-consumed"
                            )
                        snapshot_depth[depth_key]["remaining"] = max(
                            0.0, snapshot_depth[depth_key]["remaining"]
                        )
                    if shares + 1e-12 >= order.requested_shares:
                        counts["full_fills"] += 1
                        status = "full_fill"
                    else:
                        counts["partial_fills"] += 1
                        status = "partial_fill"
                    reason = "executed"
        if depth_key is not None:
            snapshot_depth_after = snapshot_depth[depth_key]["remaining"]
        retained = 0.0
        if config.reserve_until_ack and order.order_id not in send_rejected:
            retained = max(0.0, own_reservation - debit)
            reservations[order.order_id] = retained
            awaiting_ack.add(order.order_id)
            max_pending_ack_cash = max(
                max_pending_ack_cash,
                sum(reservations[oid] for oid in awaiting_ack),
            )
            max_pending_ack_orders = max(max_pending_ack_orders, len(awaiting_ack))
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
                "reserved_before": before_reserved,
                "reserved_after": sum(reservations.values()),
                "reservation_released": 0.0 if config.reserve_until_ack else own_reservation,
                "available_after": cash - sum(reservations.values()),
                "cash_debit": debit,
                "fee": fee,
                "extra_cost": extra_cost,
                "snapshot_id": order.snapshot_id,
                "snapshot_depth_before": snapshot_depth_before,
                "snapshot_depth_after": snapshot_depth_after,
                "position_before": before_position,
                "position_after": position_snapshot(order.market_id),
            }
        )
        if config.reserve_until_ack:
            ledger[-1]["reservation_consumed"] = debit
            ledger[-1]["reservation_retained_until_ack"] = retained

    if positions:
        raise RuntimeError("internal error: unsettled positions remain")
    if pair_positions:
        raise RuntimeError("internal error: unsettled pair positions remain")
    if pending_payouts:
        raise RuntimeError("internal error: unreleased payouts remain")
    if reservations:
        raise RuntimeError("internal error: unreleased cash reservations remain")
    if awaiting_ack:
        raise RuntimeError("internal error: unacknowledged orders remain")
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
        "schema_version": 8 if config.reserve_until_ack else 7,
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
            "minimum_available": available_cash_min,
            "max_reserved": max_cash_reserved,
            "reserved_final": sum(reservations.values()),
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
        "snapshot_depth": {
            "pool_count": len(snapshot_depth),
            "initial_shares": sum(row["initial"] for row in snapshot_depth.values()),
            "consumed_shares": sum(
                row["initial"] - row["remaining"] for row in snapshot_depth.values()
            ),
            "remaining_shares": sum(
                row["remaining"] for row in snapshot_depth.values()
            ),
        },
        "ledger": ledger,
        "limitations": [
            "displayed depth is only a fill upper bound",
            "no queue position, hidden liquidity or market impact is inferred",
            "unmatched inventory is marked at zero for conservative drawdown",
            "market-net and pair-local unhedged exposure are reported separately",
            "cash_release_ms must be observed or explicitly stressed; market settlement does not imply immediate spendability",
            "reserve_cash_at_send requires an observed send_ms and uses a worst-case fee reservation until the arrival event",
            "consume_snapshot_depth requires a stable snapshot_id and prevents repeated use of one recorded depth pool",
        ],
    }
    if config.reserve_until_ack:
        result["acknowledgement"] = {
            "count": ack_count,
            "max_pending_orders": max_pending_ack_orders,
            "max_pending_cash": max_pending_ack_cash,
            "final_pending_orders": len(awaiting_ack),
            "tie_policy": "settlement, cash_release, sends, arrivals, acknowledgements",
        }
        result["limitations"].append(
            "reserve_until_ack is an explicit conservative account-policy stress, not measured venue collateral availability; ack_ms must be observed or pre-fixed as a stress"
        )
    else:
        # Preserve the exact legacy JSON surface for old default callers.
        result["config"].pop("reserve_until_ack")
    if fees is not None:
        result["schema_version"] = 9
        result["market_fees"] = {
            "mode": "explicit_market_metadata",
            "config_fee_rate_applied": False,
            "records": [asdict(fees[key]) for key in sorted(fees)],
        }
        for row in ledger:
            if row["event"] in ("order_send", "order_arrival"):
                record = fees[row["market_id"]]
                row["fee_rate"] = record.fee_rate
                row["fee_evidence_sha256"] = record.evidence_sha256
        result["limitations"].append(
            "fee references and hashes are caller-supplied, not authenticated evidence; one unchanged historical fee epoch must cover each market; no current-rate fallback is used"
        )
    return result


def _load(path: Path) -> tuple[list[LegIntent], ReplayConfig]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("intents"), list):
        raise ValueError("input must be an object with an intents array")
    if "market_fees" in payload:
        raise ValueError("inline market_fees is unsupported; use --market-fees FILE")
    config = ReplayConfig(**payload.get("config", {}))
    return [LegIntent(**row) for row in payload["intents"]], config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--market-fees", type=Path,
                        help="explicit JSON array of historical MarketFee records")
    args = parser.parse_args()
    intents, config = _load(args.input)
    market_fees = None
    if args.market_fees is not None:
        raw_fees = json.loads(args.market_fees.read_text(encoding="utf-8"))
        if type(raw_fees) is not list or any(type(row) is not dict for row in raw_fees):
            raise ValueError("market fees file must be an array of objects")
        market_fees = [MarketFee(**row) for row in raw_fees]
    rendered = json.dumps(replay(intents, config, market_fees=market_fees),
                          indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

