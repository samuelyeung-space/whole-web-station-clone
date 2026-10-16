"""Auditable-cash fork of the development-only receipt-clock execution engine.

Fork of mine500_replay_v1; it does not inherit any earlier holdout freeze.

This is a displayed-depth upper-bound simulation, never evidence of actual fills.
Input JSONL is in nondecreasing local ``recv_ms`` order. Records: dataset,
market, book, decision and outcome; see ``SCHEMA`` below. Total latency already
includes any exchange waiting period. No additional 150 ms is added.

The runner deliberately does not download data, submit orders or tune signals.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import heapq
import gzip
import importlib
import inspect
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable

import mine500_constraints
from mine500_constraints import check_constraints


SCHEMA = {
    "dataset": ["recv_ms", "dataset_id", "split", "provenance"],
    "market": ["recv_ms", "market_id", "start_ms", "end_ms", "label_source"],
    "book": ["recv_ms", "market_id", "up_asks", "down_asks", "healthy", "fee_rate"],
    "decision": ["recv_ms", "market_id", "features", "feature_available_ms"],
    "outcome": ["recv_ms", "market_id", "resolved_ms", "winner", "source"],
    "constraint": ["recv_ms", "market_id", "min_order_size or up_tick_size or down_tick_size"],
}
OFFICIAL_SOURCES = frozenset({"polymarket", "gamma", "chainlink"})
EPS = 1e-9
FEE_QUANTUM = Decimal("0.00001")


@dataclass(frozen=True)
class ReplayConfig:
    full_or_zero: bool = False
    latencies_ms: tuple[int, ...] = (500, 600, 1000)
    shares: float = 5.0
    initial_cash_usd: float = 1000.0
    max_quote_age_ms: int = 250
    require_constraints: bool = False
    min_notional_usd: float = 0.0
    depth_fraction: float = 1.0
    extra_cost_per_filled_share_usd: float = 0.0
    cash_release_delay_ms: int = 0
    side_specific_books: bool = False

    def __post_init__(self):
        if type(self.cash_release_delay_ms) is not int or self.cash_release_delay_ms < 0:
            raise ValueError("cash_release_delay_ms must be a nonnegative integer")
        if not self.latencies_ms or any(type(x) is not int or x <= 0 for x in self.latencies_ms):
            raise ValueError("latencies must be positive integer total milliseconds")
        if len(set(self.latencies_ms)) != len(self.latencies_ms):
            raise ValueError("duplicate latency buckets are not independent results")
        if not _number(self.shares) or self.shares <= 0:
            raise ValueError("shares must be positive and finite")
        if not _number(self.initial_cash_usd) or self.initial_cash_usd <= 0:
            raise ValueError("initial_cash_usd must be positive and finite")
        if type(self.max_quote_age_ms) is not int or self.max_quote_age_ms < 0:
            raise ValueError("max_quote_age_ms must be a nonnegative integer")
        if type(self.require_constraints) is not bool:
            raise ValueError("require_constraints must be boolean")
        if type(self.side_specific_books) is not bool:
            raise ValueError("side_specific_books must be boolean")
        if not _number(self.depth_fraction) or not 0 < self.depth_fraction <= 1:
            raise ValueError("depth_fraction must be in (0, 1]")
        for name in ("min_notional_usd", "extra_cost_per_filled_share_usd"):
            value = getattr(self, name)
            if not _number(value) or value < 0:
                raise ValueError(name + " must be finite and nonnegative")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _validate_observed_intervals(value):
    """Validate recording coverage, never a signal-selection health filter."""
    if not isinstance(value, list) or not value:
        raise ValueError("observed_intervals_ms must be a nonempty list")
    previous_end = None
    for interval in value:
        if (not isinstance(interval, (list, tuple)) or len(interval) != 2
                or not all(_number(v) for v in interval) or interval[0] >= interval[1]):
            raise ValueError("observed_intervals_ms contains invalid endpoints")
        start, end = interval
        # Both endpoints are inclusive; touching intervals overlap as well.
        if previous_end is not None and start <= previous_end:
            raise ValueError("observed_intervals_ms must be sorted and nonoverlapping")
        previous_end = end


def _same_observed_interval(dataset, decision_ms, match_ms):
    if not dataset or "observed_intervals_ms" not in dataset:
        return True
    return any(start <= decision_ms <= match_ms <= end
               for start, end in dataset["observed_intervals_ms"])


def _levels(value):
    if not isinstance(value, list):
        raise ValueError("missing_asks")
    levels = defaultdict(float)
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("invalid_asks")
        price, size = row
        if not _number(price) or not 0 < price < 1 or not _number(size) or size < 0:
            raise ValueError("invalid_asks")
        if size:
            levels[float(price)] += float(size)
    return sorted(levels.items())


def _book(raw, now, config, requested_side=None, execution_book_sides=None):
    """Validate executable depth without reconstructing an unrecorded token.

    The default retains the original two-token contract. The explicit historical
    side lane requires an original selected-token receipt timestamp and health
    flag; envelope freshness or the other token's health cannot substitute.
    """
    if raw is None:
        return None, "missing_book"
    if config.side_specific_books:
        if requested_side not in ("Up", "Down"):
            raise ValueError("side-specific book validation requires Up or Down")
        side = requested_side.lower()
        if execution_book_sides is not None and requested_side not in execution_book_sides:
            return None, "missing_" + side + "_book"
        if raw.get(side + "_asks") is None:
            return None, "missing_" + side + "_book"
        side_recv = raw.get(side + "_recv_ms")
        if not _number(side_recv):
            return None, "missing_" + side + "_book_timestamp"
        # The stored receipt must itself be known by the enclosing event, not
        # merely by the later matching time.
        if (not _number(raw.get("recv_ms")) or raw["recv_ms"] > now or side_recv > raw["recv_ms"]
                or not 0 <= now - side_recv <= config.max_quote_age_ms):
            return None, "stale_" + side + "_book"
        health = raw.get("side_healthy")
        side_health = (health.get(requested_side, health.get(side))
                       if isinstance(health, dict) else raw.get(side + "_healthy"))
        if type(side_health) is not bool:
            return None, "missing_" + side + "_book_health"
        if side_health is not True:
            return None, "unhealthy_" + side + "_book"
        fee = raw.get("fee_rate")
        if not _number(fee) or not 0 <= fee <= 1:
            return None, "missing_or_invalid_fee"
        try:
            return {requested_side: _levels(raw[side + "_asks"]),
                    "fee_rate": float(fee)}, None
        except ValueError as exc:
            return None, str(exc) + "_" + side
    if raw.get("healthy") is not True:
        return None, "unhealthy_book"
    age = now - raw["recv_ms"]
    if age < 0 or age > config.max_quote_age_ms:
        return None, "stale_book"
    for side in ("up", "down"):
        side_recv = raw.get(side + "_recv_ms", raw["recv_ms"])
        if not _number(side_recv) or not 0 <= now - side_recv <= config.max_quote_age_ms:
            return None, "stale_" + side + "_book"
    fee = raw.get("fee_rate")
    if not _number(fee) or not 0 <= fee <= 1:
        return None, "missing_or_invalid_fee"
    try:
        return {"Up": _levels(raw.get("up_asks")),
                "Down": _levels(raw.get("down_asks")), "fee_rate": float(fee)}, None
    except ValueError as exc:
        return None, str(exc)


def _fee(shares, price, rate):
    """Unrounded Decimal formula; maker-trade fee allocation is unavailable."""
    quantity, probability, coefficient = (Decimal(str(value)) for value in (shares, price, rate))
    return quantity * coefficient * probability * (Decimal(1) - probability)


def _rounded_order_fee(raw_fee):
    # The docs specify 5 decimal places and sub-minimum charges rounding to zero.
    # Aggregate-order application and HALF_UP ties are explicit proxy assumptions;
    # the actual venue's maker-trade partition and tie handling are not observed.
    if raw_fee < FEE_QUANTUM:
        return Decimal(0)
    return raw_fee.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)


def read_jsonl(path):
    """Stream records; malformed input is an error, not a missing-market filter."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_number}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"record at line {line_number} is not an object")
            yield event


def _split_validation(dataset, candidates, config, first_decision, actual_signal_hash):
    # This experimental execution fork never inherits a freeze from the v1 engine.
    return {"requested_split": (dataset or {}).get("split", "development"),
            "effective_split": "development",
            "reasons": ["execution_v2_development_only_new_protocol_required"],
            "qualification": "No OOS qualification is possible through this development engine."}


def replay_events(events: Iterable[dict], candidates: list[dict],
                  signal_fn: Callable[[dict, dict], str | None],
                  config: ReplayConfig | None = None) -> dict:
    """Replay each candidate/latency on its own isolated starting-cash ledger.

    All feature availability timestamps are mandatory. Invalid feature rows are
    counted as decision rejections, not evaluated with future data. Once a causal
    signal qualifies, that candidate's market is consumed even if its order fails.
    The first observed price on the selected side freezes the FAK limit.
    """
    config = config or ReplayConfig()
    ids = [str(c["id"]) for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique")
    if any(not isinstance(c.get("required_features"), list) for c in candidates):
        raise ValueError("every candidate requires an explicit required_features list")
    books, markets, outcomes, constraint_updates = {}, {}, {}, {}
    seen = set()
    attempts = []
    queue = []
    sequence = 0
    diagnostics = Counter()
    invalid_by_candidate = defaultdict(Counter)
    valid_by_candidate = Counter()
    dataset = None
    first_decision = None
    last_recv = None
    ledgers = {}
    cash_events = []
    cash_sequence = 0
    open_by_market = defaultdict(list)
    for cid in ids:
        for latency in config.latencies_ms:
            ledgers[(cid, latency)] = {
                "cash": config.initial_cash_usd, "reserved": 0.0,
                "min_available": config.initial_cash_usd, "realized_pnl": 0.0,
                "peak_realized_equity": config.initial_cash_usd, "max_drawdown": 0.0,
                "locked_payout": 0.0,
            }

    def cash_event(kind, when, attempt):
        nonlocal cash_sequence
        cash_sequence += 1
        ledger = ledgers[(attempt["candidate_id"], attempt["latency_ms"])]
        cash_events.append({"candidate_id": attempt["candidate_id"],
            "latency_ms": attempt["latency_ms"], "event_sequence": cash_sequence,
            "event_ms": when, "event_type": kind,
            "cash_usd": ledger["cash"], "reserved_usd": ledger["reserved"],
            "locked_payout_usd": ledger["locked_payout"],
            "realized_pnl_usd": ledger["realized_pnl"],
            "attempt_id": attempt["attempt_id"]})

    def schedule(at, kind, payload):
        nonlocal sequence
        sequence += 1
        heapq.heappush(queue, (at, sequence, kind, payload))

    def settle(attempt, winner, when):
        if attempt["settled"] or not attempt["filled_shares"]:
            return
        ledger = ledgers[(attempt["candidate_id"], attempt["latency_ms"])]
        payout = attempt["filled_shares"] if attempt["side"] == winner else 0.0
        pnl = payout - attempt["cost_usd"]
        release_ms = when + config.cash_release_delay_ms
        if payout and config.cash_release_delay_ms:
            ledger["locked_payout"] += payout
            schedule(release_ms, "cash_release", attempt)
        else:
            ledger["cash"] += payout
        ledger["realized_pnl"] += pnl
        equity = config.initial_cash_usd + ledger["realized_pnl"]
        ledger["peak_realized_equity"] = max(ledger["peak_realized_equity"], equity)
        ledger["max_drawdown"] = max(ledger["max_drawdown"], ledger["peak_realized_equity"] - equity)
        attempt.update(settled=True, settlement_ms=when, winner=winner,
                       payout_usd=payout, net_pnl_usd=pnl,
                       cash_after_settlement=ledger["cash"], realized_equity_after_settlement=equity)
        attempt.update(cash_release_due_ms=release_ms,
                       cash_released=not bool(payout and config.cash_release_delay_ms))
        cash_event("settlement", when, attempt)

    def execute(attempt, when, horizon=None):
        ledger = ledgers[(attempt["candidate_id"], attempt["latency_ms"])]
        ledger["reserved"] -= attempt["reserved_usd_at_decision"]
        ledger["reserved"] = max(0.0, ledger["reserved"])
        cash_event("reservation_release", when, attempt)
        # Separate recordings must not be joined by a stale-but-within-age book,
        # even when both endpoints are covered by different recording intervals.
        # Retain this first attempt as censored; never use coverage to retry it.
        if not _same_observed_interval(dataset, attempt["decision_ms"], when):
            attempt["status"] = "incomplete_match_horizon"
            attempt["coverage_rejection_reason"] = "decision_and_match_not_in_same_observed_interval"
            return
        # Later label receipts do not extend recorded order-book coverage.
        recorded_end = dataset.get("coverage_end_ms") if dataset else None
        if _number(recorded_end) and when > recorded_end:
            attempt["status"] = "incomplete_match_horizon"
            return
        if horizon is not None and when > horizon:
            attempt["status"] = "incomplete_match_horizon"
            return
        market = markets.get(attempt["market_id"])
        if market is None or when >= market["end_ms"]:
            attempt["status"] = "rejected_market_closed_at_match"
            return
        book, error = _book(books.get(attempt["market_id"]), when, config, attempt["side"],
                            (dataset or {}).get("execution_book_sides"))
        if error:
            attempt["status"] = "rejected_" + error + "_at_match"
            return
        validation, constraint_error = check_constraints(
            market, books.get(attempt["market_id"]), constraint_updates.get(attempt["market_id"]),
            attempt["side"], when, config.shares, attempt["limit_price"],
            config.require_constraints, config.min_notional_usd)
        attempt["constraints_at_match"] = validation
        if constraint_error:
            attempt["status"] = "rejected_" + constraint_error + "_at_match"
            return
        if abs(book["fee_rate"] - attempt["fee_rate_decision"]) > EPS:
            attempt["status"] = "rejected_fee_changed_after_decision"
            return
        remaining, notional, raw_fee = config.shares, 0.0, Decimal(0)
        for price, size in book[attempt["side"]]:
            if price > attempt["limit_price"] + EPS or remaining <= EPS:
                break
            quantity = min(size * config.depth_fraction, remaining)
            notional += quantity * price
            raw_fee += _fee(quantity, price, book["fee_rate"])
            remaining -= quantity
        if config.full_or_zero and remaining > EPS:
            attempt["status"] = "no_fill_insufficient_full_depth"
            attempt["net_pnl_usd"] = 0.0
            return
        filled = config.shares - remaining
        fees = float(_rounded_order_fee(raw_fee))
        extra_cost = filled * config.extra_cost_per_filled_share_usd
        cost = notional + fees + extra_cost
        if cost > ledger["cash"] + EPS:
            raise AssertionError("reserved order cost exceeds ledger cash")
        ledger["cash"] -= cost
        ledger["min_available"] = min(ledger["min_available"], ledger["cash"] - ledger["reserved"])
        attempt.update(status="no_fill" if filled <= EPS else ("filled_full" if remaining <= EPS else "filled_partial"),
                       filled_shares=filled, fill_vwap=notional / filled if filled > EPS else None,
                       fees_usd=fees, extra_cost_usd=extra_cost, cost_usd=cost, cash_after_match=ledger["cash"],
                       unrounded_fees_usd=float(raw_fee),
                       fee_rounding_adjustment_usd=float(_rounded_order_fee(raw_fee) - raw_fee),
                       book_recv_ms_at_match=books[attempt["market_id"]]["recv_ms"])
        cash_event("fill" if filled > EPS else "no_fill", when, attempt)
        if filled > EPS:
            open_by_market[attempt["market_id"]].append(attempt)
            outcome = outcomes.get(attempt["market_id"])
            if outcome and outcome["known_ms"] <= when:
                settle(attempt, outcome["winner"], when)
        else:
            attempt["net_pnl_usd"] = 0.0

    def drain(before, inclusive=False, horizon=None):
        while queue and (queue[0][0] < before or (inclusive and queue[0][0] == before)):
            when, _, kind, payload = heapq.heappop(queue)
            if kind == "match":
                execute(payload, when, horizon)
            elif kind == "cash_release":
                if horizon is None or when <= horizon:
                    ledger = ledgers[(payload["candidate_id"], payload["latency_ms"])]
                    ledger["cash"] += payload["payout_usd"]
                    ledger["locked_payout"] -= payload["payout_usd"]
                    payload.update(cash_released=True, cash_after_release=ledger["cash"])
                    cash_event("payout_release", when, payload)
            elif horizon is None or when <= horizon:
                for attempt in open_by_market.get(payload, []):
                    settle(attempt, outcomes[payload]["winner"], when)

    def process(event):
        nonlocal dataset, first_decision
        now, kind = event["recv_ms"], event.get("type")
        diagnostics["input_" + str(kind)] += 1
        if kind == "dataset":
            if dataset is not None:
                raise ValueError("multiple dataset headers are not allowed")
            if "observed_intervals_ms" in event:
                _validate_observed_intervals(event["observed_intervals_ms"])
            dataset = event
            if event.get("book_resolution") == "decision_and_match_projection":
                supported = event.get("supported_latencies_ms")
                grid = event.get("decision_grid_ms")
                if (not isinstance(supported, list) or
                        any(type(v) is not int or v <= 0 for v in supported) or
                        not set(config.latencies_ms).issubset(supported)):
                    raise ValueError("projection does not support requested total latency")
                if type(grid) is not int or grid <= 0:
                    raise ValueError("projection requires fixed positive decision_grid_ms")
                if config.side_specific_books:
                    sides = event.get("execution_book_sides")
                    if (not isinstance(sides, list) or not sides
                            or any(side not in ("Up", "Down") for side in sides)
                            or len(set(sides)) != len(sides)):
                        raise ValueError("side-specific projection requires explicit unique execution_book_sides")
            return
        market_id = event.get("market_id")
        if not isinstance(market_id, str) or not market_id:
            diagnostics["rejected_missing_market_id"] += 1
            return
        if kind == "market":
            start, end = event.get("start_ms"), event.get("end_ms")
            if not _number(start) or not _number(end) or end - start != 300000 or event.get("label_source") not in OFFICIAL_SOURCES:
                diagnostics["rejected_invalid_market_metadata"] += 1
                markets.pop(market_id, None)
                return
            previous = markets.get(market_id)
            if previous and any(previous[key] != event[key] for key in ("start_ms", "end_ms", "label_source")):
                raise ValueError("market settlement definition changed during replay")
            markets[market_id] = event
            return
        if kind == "constraint":
            previous = constraint_updates.get(market_id, {})
            merged = dict(previous)
            for name in ("min_order_size", "up_tick_size", "down_tick_size"):
                if name in event:
                    merged[name] = event[name]
                    merged[name + "_available_ms"] = event.get(name + "_available_ms", now)
                    merged[name + "_effective_ms"] = event.get(name + "_effective_ms", merged[name + "_available_ms"])
                    merged[name + "_source"] = event.get(name + "_source", event.get("constraints_source"))
            merged["recv_ms"] = now
            constraint_updates[market_id] = merged
            return
        if kind == "book":
            if dataset and dataset.get("book_resolution") == "decision_and_match_projection":
                if config.side_specific_books:
                    if any(side.lower() + "_recv_ms" not in event
                           for side in dataset["execution_book_sides"]):
                        raise ValueError("projected books must preserve declared original side receipt timestamps")
                elif any(name not in event for name in ("up_recv_ms", "down_recv_ms")):
                    raise ValueError("projected books must preserve both original side receipt timestamps")
            # Invalid new books replace old books too. Never resurrect stale depth.
            books[market_id] = event
            return
        if kind == "outcome":
            market = markets.get(market_id)
            resolved = event.get("resolved_ms")
            if (market is None or event.get("winner") not in ("Up", "Down")
                    or event.get("source") not in OFFICIAL_SOURCES
                    or event.get("source") != market["label_source"]
                    or not _number(resolved) or resolved < market["end_ms"]):
                diagnostics["rejected_invalid_or_unofficial_outcome"] += 1
                return
            prior = outcomes.get(market_id)
            if prior:
                if prior["winner"] != event["winner"]:
                    raise ValueError("conflicting official outcomes")
                diagnostics["duplicate_outcome"] += 1
                return
            known = max(now, resolved)
            outcomes[market_id] = {"winner": event["winner"], "known_ms": known}
            if known == now:
                for attempt in open_by_market.get(market_id, []):
                    settle(attempt, event["winner"], known)
            else:
                schedule(known, "settle", market_id)
            return
        if kind != "decision":
            diagnostics["rejected_unknown_record_type"] += 1
            return
        if (dataset and dataset.get("book_resolution") == "decision_and_match_projection"
                and abs(now / dataset["decision_grid_ms"] - round(now / dataset["decision_grid_ms"])) > 1e-6):
            raise ValueError("decision outside registered projection grid")
        first_decision = now if first_decision is None else min(first_decision, now)
        features, available = event.get("features"), event.get("feature_available_ms")
        market = markets.get(market_id)
        for candidate in candidates:
            cid = str(candidate["id"])
            if (cid, market_id) in seen:
                continue
            error = None
            if market is None or not market["start_ms"] <= now < market["end_ms"]:
                error = "missing_market_or_outside_window"
            elif not isinstance(features, dict) or not isinstance(available, dict):
                error = "missing_feature_maps"
            else:
                # Only the first invalid field is attributed. Keep that
                # diagnostic stable across Python hash seeds and processes.
                for name in sorted(set(candidate["required_features"]) | {"tau_s"}):
                    if not _number(features.get(name)):
                        error = "missing_or_invalid_feature:" + name
                        break
                    if not _number(available.get(name)):
                        error = "missing_feature_availability:" + name
                        break
                    if available[name] > now:
                        error = "future_feature:" + name
                        break
                if error is None and (not 0 <= features["tau_s"] <= 300 or
                        abs(features["tau_s"] - (market["end_ms"] - now) / 1000) > 0.001):
                    error = "invalid_tau_or_market_clock"
            if error:
                invalid_by_candidate[cid][error] += 1
                continue
            valid_by_candidate[cid] += 1
            try:
                # Do not expose an unchecked extra column to the signal function.
                visible = {name: features[name] for name in set(candidate["required_features"]) | {"tau_s"}}
                side = signal_fn(candidate, visible)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                invalid_by_candidate[cid]["signal_error:" + type(exc).__name__] += 1
                continue
            if side is None:
                continue
            if side not in ("Up", "Down"):
                raise ValueError(f"invalid signal return for {cid}: {side!r}")
            seen.add((cid, market_id))
            book, book_error = _book(books.get(market_id), now, config, side,
                                     (dataset or {}).get("execution_book_sides"))
            for latency in config.latencies_ms:
                ledger = ledgers[(cid, latency)]
                attempt = {"candidate_id": cid, "latency_ms": latency, "market_id": market_id,
                    "attempt_id": str(len(attempts) + 1),
                    "decision_ms": now, "match_ms": now + latency, "side": side,
                    "requested_shares": config.shares, "limit_price": None,
                    "submitted": False,
                    "fee_rate_decision": None, "status": "scheduled", "filled_shares": 0.0,
                    "fill_vwap": None, "fees_usd": 0.0, "unrounded_fees_usd": 0.0,
                    "fee_rounding_adjustment_usd": 0.0, "extra_cost_usd": 0.0, "cost_usd": 0.0,
                    "constraints_at_decision": None, "constraints_at_match": None,
                    "settled": False, "settlement_ms": None, "winner": None,
                    "payout_usd": None, "net_pnl_usd": None,
                    "cash_before_decision": ledger["cash"], "cash_after_match": None,
                    "cash_after_settlement": None, "reserved_usd_at_decision": 0.0,
                    "reserved_fee_usd_at_decision": 0.0}
                attempts.append(attempt)
                if book_error:
                    attempt["status"] = "rejected_" + book_error + "_at_decision"
                    continue
                if not book[side]:
                    attempt["status"] = "rejected_no_depth_at_decision"
                    continue
                limit = book[side][0][0]
                fee = book["fee_rate"]
                attempt.update(limit_price=limit, fee_rate_decision=fee)
                validation, constraint_error = check_constraints(
                    market, books.get(market_id), constraint_updates.get(market_id),
                    side, now, config.shares, limit,
                    config.require_constraints, config.min_notional_usd)
                attempt["constraints_at_decision"] = validation
                if constraint_error:
                    attempt["status"] = "rejected_" + constraint_error + "_at_decision"
                    continue
                reserved_fee = float(_fee(config.shares, limit, fee).quantize(FEE_QUANTUM, rounding=ROUND_CEILING))
                attempt["reserved_fee_usd_at_decision"] = reserved_fee
                reservation = (config.shares * limit + reserved_fee
                               + config.shares * config.extra_cost_per_filled_share_usd)
                if ledger["cash"] - ledger["reserved"] + EPS < reservation:
                    attempt["status"] = "rejected_insufficient_available_cash"
                    continue
                attempt["reserved_usd_at_decision"] = reservation
                attempt["submitted"] = True
                ledger["reserved"] += reservation
                ledger["min_available"] = min(ledger["min_available"], ledger["cash"] - ledger["reserved"])
                cash_event("reservation", now, attempt)
                schedule(now + latency, "match", attempt)

    # Buffer only one equal-timestamp group; matches see all events received by
    # their due timestamp. Decisions remain in observed receipt order within it.
    group = []
    for event in events:
        now = event.get("recv_ms")
        if not _number(now):
            raise ValueError("every record must include finite local recv_ms")
        if last_recv is not None and now < last_recv:
            raise ValueError("input is not sorted by local receipt time")
        if last_recv is not None and now != last_recv:
            drain(last_recv)
            for grouped_event in group:
                process(grouped_event)
            drain(last_recv, inclusive=True)
            group = []
            drain(now)
        group.append(event)
        last_recv = now
    if group:
        drain(last_recv)
        for event in group:
            process(event)
        drain(last_recv, inclusive=True)
    horizon = last_recv if last_recv is not None else -1
    # A declared endpoint may cover quiet periods. execute() separately ensures
    # later outcome receipts never extend the book-recording coverage endpoint.
    if dataset and _number(dataset.get("coverage_end_ms")):
        horizon = max(horizon, dataset["coverage_end_ms"])
    drain(math.inf, horizon=horizon)
    valid_provenance = bool(dataset and dataset.get("dataset_id") and isinstance(dataset.get("provenance"), dict)
                            and dataset["provenance"].get("source"))
    try:
        signal_path = inspect.getsourcefile(signal_fn)
        actual_signal_hash = hashlib.sha256(Path(signal_path).read_bytes()).hexdigest() if signal_path else None
    except (TypeError, OSError):
        actual_signal_hash = None
    split = _split_validation(dataset, candidates, config, first_decision, actual_signal_hash)
    by_key = defaultdict(list)
    for attempt in attempts:
        by_key[(attempt["candidate_id"], attempt["latency_ms"])].append(attempt)
    summary = []
    for cid in ids:
        for latency in config.latencies_ms:
            rows = by_key[(cid, latency)]
            ledger = ledgers[(cid, latency)]
            statuses = Counter(row["status"] for row in rows)
            filled = [row for row in rows if row["filled_shares"] > EPS]
            settled = [row for row in filled if row["settled"]]
            qty = sum(row["filled_shares"] for row in filled)
            incomplete = any(row["status"] == "incomplete_match_horizon" for row in rows) or len(filled) != len(settled)
            missing_execution_inputs = any(any(marker in row["status"] for marker in (
                "missing_book", "missing_up_book", "missing_down_book",
                "missing_or_invalid_fee", "missing_asks", "invalid_asks",
                "fee_changed_after_decision", "invalid_min_order_size_metadata",
                "missing_required_constraints", "invalid_constraint_timestamp", "invalid_up_tick_size_metadata",
                "invalid_down_tick_size_metadata")) for row in rows)
            if not valid_provenance:
                status, pnl = "blocked_missing_data_provenance", None
            elif not diagnostics["input_decision"]:
                status, pnl = "blocked_no_decision_data", None
            elif not valid_by_candidate[cid]:
                status, pnl = "blocked_no_valid_feature_rows", None
            elif not rows:
                status, pnl = "evaluated_no_signal", None
            elif any("rejected_below_market_min_order_size" in name for name in statuses):
                status, pnl = "blocked_below_market_min_order_size", None
            elif missing_execution_inputs:
                status, pnl = "incomplete_missing_execution_inputs", None
            elif incomplete:
                status, pnl = "incomplete_unresolved_or_truncated", None
            elif not filled:
                status, pnl = "evaluated_no_fills", 0.0
            else:
                status, pnl = "simulated_complete", ledger["realized_pnl"]
            summary.append({"candidate_id": cid, "latency_ms": latency, "status": status,
                "effective_split": split["effective_split"], "attempts": len(rows),
                "requested_shares": config.shares,
                "submitted_orders": sum(r["submitted"] for r in rows),
                "markets": len({r["market_id"] for r in rows}),
                "decision_utc_days": len({int(r["decision_ms"] // 86400000) for r in rows}),
                "valid_feature_rows": valid_by_candidate[cid],
                "invalid_decision_reasons": dict(invalid_by_candidate[cid]),
                "status_counts": dict(statuses), "full_fills": statuses["filled_full"],
                "missing_execution_inputs": missing_execution_inputs,
                "partial_fills": statuses["filled_partial"], "filled_shares": qty,
                "settled_positions": len(settled), "unresolved_positions": len(filled) - len(settled),
                "net_pnl_usd": pnl, "realized_pnl_usd": ledger["realized_pnl"] if rows and valid_provenance else None,
                "net_pnl_per_filled_share_usd": pnl / qty if pnl is not None and qty else None,
                "fees_usd": sum(r["fees_usd"] for r in rows),
                "unrounded_fees_usd": sum(r["unrounded_fees_usd"] for r in rows),
                "fee_rounding_adjustment_usd": sum(r["fee_rounding_adjustment_usd"] for r in rows),
                "extra_cost_usd": sum(r["extra_cost_usd"] for r in rows),
                "constraints_validated_attempts": sum(bool(r["constraints_at_decision"] and r["constraints_at_decision"]["validated"]) for r in rows),
                "filled_positions_with_validated_constraints": sum(bool(r["constraints_at_decision"] and r["constraints_at_decision"]["validated"] and r["constraints_at_match"] and r["constraints_at_match"]["validated"]) for r in filled),
                "initial_cash_usd": config.initial_cash_usd,
                "ending_cash_usd": ledger["cash"], "min_available_cash_usd": ledger["min_available"],
                "ending_reserved_usd": ledger["reserved"],
                "ending_locked_payout_usd": ledger["locked_payout"],
                "cash_release_delay_ms": config.cash_release_delay_ms,
                "max_realized_drawdown_usd": ledger["max_drawdown"],
                "drawdown_basis": "realized_only_no_mark_to_market", "confirmed_edge": False})
    return {"schema_version": "mine500_replay_v2_development", "dataset": dataset,
        "config": asdict(config), "candidate_sha256": fingerprint(candidates),
        "execution_sha256": fingerprint(asdict(config)), "split_validation": split,
        "replay_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "constraints_code_sha256": hashlib.sha256(Path(mine500_constraints.__file__).read_bytes()).hexdigest(),
        "simulation_only": True, "fill_model": "displayed_full_or_zero_sensitivity_not_actual_FAK" if config.full_or_zero else "displayed_depth_fraction_partial_FAK_sensitivity_not_actual_fills",
        "research_status": "development_only_new_execution_version",
        "constraints_mode": "required" if config.require_constraints else "upper_bound_missing_constraints_not_validated",
        "stress_assumptions": {"depth_fraction": config.depth_fraction,
            "cash_release_delay_ms": config.cash_release_delay_ms,
            "cash_release_basis": "delay after local official outcome knowledge; assumed redemption, not observed",
            "min_notional_usd": config.min_notional_usd,
            "extra_cost_per_filled_share_usd": config.extra_cost_per_filled_share_usd,
            "interpretation": "Explicit assumptions, not observed competition, venue minimum-notional, or actual execution costs."},
        "fee_calculation": "Decimal sum(quantity * fee_rate * fill_price * (1-fill_price)); aggregate-order proxy: raw fee below 0.00001 becomes zero, otherwise rounded to 5 decimal places with assumed HALF_UP ties; no rebates",
        "fee_reservation": "Unrounded full-order fee at fixed limit rounded upward to 5 decimal places",
        "fee_rounding_limitation": "Official precision is 5 decimal places and fees smaller than 0.00001 round to zero. Aggregate-order scope and HALF_UP tie handling are assumptions. Actual maker-trade partitions and venue rounding granularity are unavailable; this is not an exact exchange bill.",
        "fee_precision_reference": {"url": "https://docs.polymarket.com/trading/fees", "checked_utc_date": "2026-10-11"},
        "latency_clock": "local_decision_to_match_total_including_venue_wait",
        "book_validation_mode": "selected_side_original_receipt_health_and_depth" if config.side_specific_books else "both_sides_required",
        "recording_coverage_policy": "decision_and_match_must_share_one_inclusive_observed_interval_when_supplied",
        "capital_scope": "each_candidate_and_latency_has_independent_cash_do_not_sum",
        "diagnostics": dict(diagnostics), "summary": summary, "attempts": attempts,
        "cash_events": cash_events, "cash_trace_version": "actual_engine_mutations_v1"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--latencies", default="500,600,1000")
    parser.add_argument("--shares", type=float, default=5.0)
    parser.add_argument("--initial-cash", type=float, default=1000.0)
    parser.add_argument("--max-quote-age-ms", type=int, default=250)
    parser.add_argument("--catalog-module", default="mine500_catalog")
    parser.add_argument("--require-constraints", action="store_true")
    parser.add_argument("--min-notional-usd", type=float, default=0.0)
    parser.add_argument("--depth-fraction", type=float, default=1.0)
    parser.add_argument("--extra-cost-per-filled-share-usd", type=float, default=0.0)
    parser.add_argument("--cash-release-delay-ms", type=int, default=0)
    parser.add_argument("--full-or-zero", action="store_true")
    args = parser.parse_args(argv)
    catalog = importlib.import_module(args.catalog_module)
    candidates = catalog.make_candidates()
    config = ReplayConfig(latencies_ms=tuple(int(s) for s in args.latencies.split(",")),
                          shares=args.shares, initial_cash_usd=args.initial_cash,
                          max_quote_age_ms=args.max_quote_age_ms,
                          require_constraints=args.require_constraints,
                          min_notional_usd=args.min_notional_usd, depth_fraction=args.depth_fraction,
                          extra_cost_per_filled_share_usd=args.extra_cost_per_filled_share_usd,
                          cash_release_delay_ms=args.cash_release_delay_ms, full_or_zero=args.full_or_zero)
    args.out.mkdir(parents=True, exist_ok=True)
    blocked = None
    if args.input is None or not args.input.is_file():
        blocked = "No normalized input file supplied; no replay or PnL calculation occurred."
        events = []
    else:
        events = read_jsonl(args.input)
    result = replay_events(events, candidates, catalog.signal, config)
    if blocked:
        result["blocked_reason"] = blocked
    result["candidate_count"] = len(candidates)
    result["required_feature_coverage"] = dict(Counter(f for c in candidates for f in c["required_features"]))
    source_path = Path(catalog.__file__)
    result["signal_code_sha256_actual"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    # A claimed signal hash is not enough: verify against code actually executed.
    if (result["split_validation"]["effective_split"] != "development" and
            (result["dataset"] or {}).get("signal_code_sha256") != result["signal_code_sha256_actual"]):
        result["split_validation"]["effective_split"] = "development"
        result["split_validation"]["reasons"].append("executed_signal_code_hash_mismatch")
        for row in result["summary"]:
            row["effective_split"] = "development"
    with (args.out / "attempts.jsonl").open("w", encoding="utf-8") as handle:
        for row in result.pop("attempts"):
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_count": len(candidates), "latency_count": len(config.latencies_ms),
                      "input_available": not bool(blocked), "simulation_only": True,
                      "summary_path": str(args.out / "summary.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


