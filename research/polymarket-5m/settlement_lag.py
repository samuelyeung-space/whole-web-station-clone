"""Strict paper replay for BTC five-minute final-TWAP settlement latency.

The signal is not a price forecast.  Polymarket's public ``twap_sixty`` feed
publishes the opening and closing 60-second values used by the contract.  Once
the closing value is locally received, their comparison fixes the winning
token.  This module tests whether five directly quoted shares still survive at
a predeclared limit after the measured order path.

Official outcomes are attached only for post-trade auditing and PnL.  They are
never consulted when choosing the signal direction.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from itertools import groupby
from pathlib import Path
from typing import Any, cast

import h_replay
import h_replay_run
import settlement_lag_archive as h_replay_archive
import settlement_proxy

FINAL_VARIANT = "twap60_final"
PREFINAL_VARIANT = "twap60_prefinal_1s"
WRONG_SIDE_VARIANT = "twap60_wrong_side"
FREEZE_PATH = Path(__file__).with_name("forward") / "settlement-lag-freeze.json"
FREEZE_SHA256_PATH = FREEZE_PATH.with_suffix(".sha256")
INPUT_CLOCK_DOMAIN = "same_host_unix_realtime_ns"
EXECUTION_METADATA_SCHEMA = "settlement-lag-market-metadata-v2"
CLOCK_ATTESTATION_SCHEMA = "settlement-lag-clock-attestation-v2"
STANDARD_INPUT_MANIFEST_SCHEMA = "settlement-lag-standard-input-v2"
CLOB_CUSTODY_SCOPE = "same-host-active-open-fd-and-post-close-identity"
STANDARD_ARTIFACT_FILES = (
    "manifest.json",
    "input-binding.json",
    "source_events.jsonl.gz",
    "clob_events.jsonl.gz",
    "market_registry.csv.gz",
    "market_outcomes.csv.gz",
)


@dataclass(frozen=True)
class TwapPoint:
    payload_ms: float
    push_ms: float
    receive_ms: float
    value: float
    receive_ns: int | None = None


@dataclass(frozen=True)
class TwapTape:
    points: dict[float, TwapPoint]
    ambiguous_payload_ms: frozenset[float]
    audit: dict[str, int]
    first_conflict_receive_ms: dict[float, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyConfig:
    opening_offset_ms: float = -2_000.0
    closing_offset_ms: float = -2_000.0
    prefinal_offset_ms: float = -3_000.0
    local_path_ms: float = 0.86
    venue_hold_ms: float = 150.0
    evaluation_ms: tuple[float, ...] = (200.0, 300.0, 500.0)
    primary_evaluation_ms: float = 300.0
    theta: float = 0.025
    tick: float = 0.01
    fee_rate: float = 0.07
    base_shares: float = 5.0
    pilot_min_usd: float = 1.0
    max_order_usd: float = 10.0
    family_tests: int = 100
    minimum_fills: int = 1_000
    minimum_days: int = 7
    capital_annual_rate: float = 0.10
    redemption_cost_usd: float = 0.01


@dataclass(frozen=True)
class SettlementSignal:
    variant: str
    market_id: str
    slot: int
    decision_ms: float
    direction: str
    opening: float
    closing: float
    margin: float
    proof_payload_ms: float
    proof_push_ms: float
    proof_lead_ms: float
    actual_winner: str | None
    source_revision_after_decision: bool = False
    decision_ns: int | None = None


def strategy_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def expected_freeze_sha256(path: str | Path = FREEZE_PATH) -> str:
    source = Path(path)
    sidecar = (
        FREEZE_SHA256_PATH
        if source.resolve() == FREEZE_PATH.resolve()
        else source.with_suffix(".sha256")
    )
    expected = sidecar.read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("invalid settlement-lag freeze fingerprint sidecar")
    return expected


EXPECTED_FREEZE_SHA256 = expected_freeze_sha256()


def load_freeze(path: str | Path = FREEZE_PATH) -> dict[str, Any]:
    source = Path(path)
    raw = source.read_bytes()
    payload = json.loads(raw)
    actual = hashlib.sha256(raw).hexdigest()
    expected = expected_freeze_sha256(source)
    if actual != expected:
        raise ValueError(
            f"settlement-lag freeze fingerprint mismatch: {actual} != {expected}"
        )
    return payload


def config_from_freeze(frozen: Mapping[str, Any]) -> StrategyConfig:
    proof = frozen["proof"]
    execution = frozen["execution"]
    controls = frozen["controls"]
    stopping = frozen["stopping"]
    twap_coverage = frozen.get("twap_coverage")
    recorders = frozen.get("recorders")
    config = StrategyConfig(
        opening_offset_ms=float(proof["opening_payload_offset_ms"]),
        closing_offset_ms=float(proof["closing_payload_offset_ms"]),
        prefinal_offset_ms=float(controls["prefinality_payload_offset_ms"]),
        local_path_ms=float(execution["local_path_ms"]),
        venue_hold_ms=float(execution["venue_hold_ms"]),
        evaluation_ms=tuple(float(value) for value in execution["evaluation_ms"]),
        primary_evaluation_ms=float(execution["primary_evaluation_ms"]),
        theta=float(execution["theta"]),
        tick=float(execution["tick"]),
        fee_rate=float(execution["fee_rate"]),
        base_shares=float(execution["base_shares"]),
        pilot_min_usd=float(execution["minimum_notional_usd"]),
        max_order_usd=float(execution["maximum_order_usd"]),
        family_tests=int(stopping["family_tests"]),
        minimum_fills=int(stopping["minimum_primary_full_fills"]),
        minimum_days=int(stopping["minimum_utc_days"]),
        capital_annual_rate=float(execution.get("capital_annual_rate", 0.10)),
        redemption_cost_usd=float(execution.get("redemption_cost_usd", 0.01)),
    )
    if frozen.get("strategy_id") != "twap60-final-race-v3":
        raise ValueError("unexpected settlement-lag strategy id")
    if execution.get("order_type") != "FAK" or not math.isclose(
        float(execution["fixed_limit"]),
        0.97,
        abs_tol=1e-12,
    ):
        raise ValueError("settlement-lag execution contract drift")
    if execution.get("depth_mode") != "effective_complementary_single_path_v2":
        raise ValueError("unexpected settlement-lag depth mode")
    if execution.get("clock_scope") != CLOB_CUSTODY_SCOPE:
        raise ValueError("unexpected settlement-lag clock scope")
    if twap_coverage != {
        "required_markets_per_utc_day": 288,
        "required_payload_offsets_ms": {
            "opening": -2_000,
            "prefinal": -3_000,
            "final": -2_000,
        },
        "required_receive_segments": 25,
        "maximum_missing_required_payload_keys": 0,
    }:
        raise ValueError("unexpected settlement-lag TWAP coverage contract")
    if not isinstance(recorders, Mapping) or set(recorders) != {
        "clob",
        "twap",
        "metadata",
    }:
        raise ValueError("unexpected settlement-lag recorder contract")
    if execution.get("execution_metadata_source") != "recorded_gamma_sidecar_v1":
        raise ValueError("unexpected settlement-lag execution metadata source")
    if execution.get("after_market_close_policy") != (
        "no_fill_primary_retain_raw_sensitivity"
    ):
        raise ValueError("unexpected settlement-lag market-close policy")
    execution_config(config)
    return config


def parse_twap_frame(
    receive_ms: float,
    text: str,
    *,
    receive_ns: int | None = None,
) -> TwapPoint | None:
    """Parse one direct final-TWAP frame while preserving all three clocks."""
    payload = json.loads(text)
    if (
        not isinstance(payload, Mapping)
        or payload.get("topic") != "crypto_prices_twap_sixty"
    ):
        return None
    data = payload.get("payload")
    if not isinstance(data, Mapping):
        raise TypeError("twap frame has no payload object")
    if (
        str(data.get("symbol", "")).lower() != "btc/usd"
        or int(data.get("window_s", 0)) != 60
    ):
        return None
    point = TwapPoint(
        payload_ms=float(data["timestamp"]),
        push_ms=float(payload["timestamp"]),
        receive_ms=float(receive_ms),
        value=float(data["value"]),
        receive_ns=receive_ns,
    )
    if (
        not all(
            math.isfinite(value)
            for value in (
                point.payload_ms,
                point.push_ms,
                point.receive_ms,
                point.value,
            )
        )
        or point.value <= 0
        or (point.receive_ns is not None and point.receive_ns < 0)
        or (
            point.receive_ns is not None
            and point.receive_ms != point.receive_ns / 1_000_000.0
        )
    ):
        raise ValueError("invalid twap numeric value")
    return point


def _collect_twap_points(
    rows: Iterable[tuple[float, int | None, str]],
) -> TwapTape:
    points: dict[float, TwapPoint] = {}
    ambiguous: set[float] = set()
    first_conflict_receive_ms: dict[float, float] = {}
    audit: Counter[str] = Counter()
    active_epoch: int | None = None
    previous_order: int | float = -math.inf
    for receive_ms, receive_ns, text in rows:
        receive_ms = float(receive_ms)
        order: int | float = receive_ns if receive_ns is not None else receive_ms
        if order < previous_order:
            raise ValueError("TWAP receipt clock regressed")
        previous_order = order
        try:
            raw = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            audit["invalid_frames"] += 1
            continue
        marker = raw.get("_recorder") if isinstance(raw, Mapping) else None
        if (
            isinstance(marker, Mapping)
            and marker.get("schema") == "recorder-lifecycle-v1"
        ):
            try:
                epoch = int(marker["epoch"])
            except (KeyError, TypeError, ValueError):
                audit["invalid_frames"] += 1
                continue
            if marker.get("kind") == "connection":
                active_epoch = epoch
                audit["connections"] += 1
            elif marker.get("kind") == "disconnect" and active_epoch == epoch:
                active_epoch = None
                audit["disconnects"] += 1
            continue
        if active_epoch is None:
            if (
                isinstance(raw, Mapping)
                and raw.get("topic") == "crypto_prices_twap_sixty"
            ):
                audit["outside_connection_epoch"] += 1
            continue
        try:
            point = parse_twap_frame(receive_ms, text, receive_ns=receive_ns)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError):
            audit["invalid_frames"] += 1
            continue
        if point is None:
            continue
        payload_key = point.payload_ms
        previous = points.get(payload_key)
        if previous is None:
            points[payload_key] = point
            audit["accepted"] += 1
        elif math.isclose(previous.value, point.value, rel_tol=0.0, abs_tol=1e-12):
            audit["same_value_duplicates"] += 1
        else:
            if payload_key not in ambiguous:
                ambiguous.add(payload_key)
                first_conflict_receive_ms[payload_key] = point.receive_ms
                audit["conflicting_revisions"] += 1
    for audit_key in (
        "connections",
        "disconnects",
        "accepted",
        "same_value_duplicates",
        "conflicting_revisions",
        "outside_connection_epoch",
        "invalid_frames",
    ):
        audit.setdefault(audit_key, 0)
    return TwapTape(
        points,
        frozenset(ambiguous),
        dict(audit),
        first_conflict_receive_ms,
    )


def collect_twap_points(rows: Iterable[tuple[float, str]]) -> TwapTape:
    """Build a revision-safe tape inside explicit recorder connection epochs."""
    return _collect_twap_points(
        (float(receive_ms), None, text) for receive_ms, text in rows
    )


def collect_twap_points_exact(rows: Iterable[tuple[int, str]]) -> TwapTape:
    """Build the tape without rounding epoch nanoseconds through a float first."""
    def exact_rows() -> Iterator[tuple[float, int, str]]:
        for receive_ns, text in rows:
            if isinstance(receive_ns, bool):
                raise ValueError("TWAP receipt nanoseconds must be an integer")
            receive_ns = int(receive_ns)
            if receive_ns < 0:
                raise ValueError("TWAP receipt nanoseconds must be non-negative")
            yield receive_ns / 1_000_000.0, receive_ns, text

    return _collect_twap_points(exact_rows())


def iter_raw_lines(paths: Sequence[str | Path]) -> Iterator[tuple[float, str]]:
    for receive_ns, text in iter_raw_lines_exact(paths):
        yield receive_ns / 1_000_000.0, text


def iter_raw_lines_exact(paths: Sequence[str | Path]) -> Iterator[tuple[int, str]]:
    for path in sorted(Path(value) for value in paths):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                clock, separator, text = line.rstrip("\n").partition("\t")
                if not separator:
                    raise ValueError(
                        f"{path.name}:{line_number}: missing receipt timestamp"
                    )
                try:
                    receive_ns = int(clock)
                    if receive_ns < 0:
                        raise ValueError
                    yield receive_ns, text
                except ValueError as exc:
                    raise ValueError(
                        f"{path.name}:{line_number}: invalid receipt timestamp"
                    ) from exc


def require_exact_clob_receipts(
    events: Iterable[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Reject formal CLOB streams that lost their original integer receipt clock."""
    previous_ns = -1
    for event in events:
        value = event.get("recv_ns")
        if isinstance(value, bool) or value is None:
            raise ValueError("formal CLOB event lacks exact recv_ns")
        receive_ns = int(value)
        if receive_ns < previous_ns:
            raise ValueError("formal CLOB recv_ns regressed")
        if float(event["recv_ms"]) != receive_ns / 1_000_000.0:
            raise ValueError("formal CLOB recv_ns/recv_ms drift")
        previous_ns = receive_ns
        yield event


def _clob_batches_exact(
    events: Iterable[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Batch only events sharing the exact original receipt timestamp."""
    def receipt_ns(event: Mapping[str, Any]) -> int:
        value = event.get("recv_ns")
        return (
            int(value)
            if value is not None
            else int(round(float(event["recv_ms"]) * 1_000_000.0))
        )

    for receive_ns, rows in groupby(events, key=receipt_ns):
        group = list(rows)
        yield {
            "kind": "clob_batch",
            "recv_ms": float(group[0]["recv_ms"]),
            "recv_ns": receive_ns,
            "seq": int(group[0]["seq"]),
            "events": group,
        }


def _winner(closing: float, opening: float) -> str:
    return "Up" if closing >= opening else "Down"


def _signal(
    variant: str,
    market_id: str,
    slot: int,
    opening: TwapPoint,
    proof: TwapPoint,
    direction: str,
    actual_winner: str | None,
    source_revision_after_decision: bool = False,
) -> SettlementSignal:
    market_close_ms = (slot + 300) * 1_000.0
    return SettlementSignal(
        variant=variant,
        market_id=market_id,
        slot=slot,
        decision_ms=proof.receive_ms,
        direction=direction,
        opening=opening.value,
        closing=proof.value,
        margin=abs(proof.value - opening.value) / opening.value,
        proof_payload_ms=proof.payload_ms,
        proof_push_ms=proof.push_ms,
        proof_lead_ms=market_close_ms - proof.receive_ms,
        actual_winner=actual_winner,
        source_revision_after_decision=source_revision_after_decision,
        decision_ns=proof.receive_ns,
    )


def _revision_timing(
    tape: TwapTape, payload_ms: float, decision_ms: float
) -> str | None:
    if payload_ms not in tape.ambiguous_payload_ms:
        return None
    conflict_ms = tape.first_conflict_receive_ms.get(payload_ms, -math.inf)
    return "known" if conflict_ms <= decision_ms else "later"


def generate_signals(
    mappings: Iterable[Mapping[str, Any]],
    tape: TwapTape,
    official_outcomes: Mapping[str, str],
    config: StrategyConfig | None = None,
) -> tuple[dict[str, list[SettlementSignal]], dict[str, int]]:
    """Generate deterministic and placebo signals without reading an outcome for direction."""
    config = config or StrategyConfig()
    output: dict[str, list[SettlementSignal]] = {
        FINAL_VARIANT: [],
        PREFINAL_VARIANT: [],
        WRONG_SIDE_VARIANT: [],
    }
    audit: Counter[str] = Counter()
    for mapping in sorted(
        mappings, key=lambda row: (int(row["slot"]), str(row["market_id"]))
    ):
        audit["markets"] += 1
        market_id = str(mapping["market_id"])
        slot = int(mapping["slot"])
        start_ms = slot * 1_000.0
        close_ms = (slot + 300) * 1_000.0
        opening_key = start_ms + config.opening_offset_ms
        final_key = close_ms + config.closing_offset_ms
        prefinal_key = close_ms + config.prefinal_offset_ms
        opening = tape.points.get(opening_key)
        if opening is None:
            audit["missing_opening"] += 1
            continue
        prefinal = tape.points.get(prefinal_key)
        if prefinal is None:
            audit["missing_prefinal"] += 1
        elif opening.receive_ms > prefinal.receive_ms:
            audit["opening_received_after_prefinal_proof"] += 1
        elif (
            _revision_timing(tape, opening_key, prefinal.receive_ms) == "known"
            or _revision_timing(tape, prefinal_key, prefinal.receive_ms) == "known"
        ):
            audit["ambiguous_prefinal"] += 1
        elif prefinal.receive_ms + config.local_path_ms < close_ms:
            output[PREFINAL_VARIANT].append(
                _signal(
                    PREFINAL_VARIANT,
                    market_id,
                    slot,
                    opening,
                    prefinal,
                    _winner(prefinal.value, opening.value),
                    official_outcomes.get(market_id),
                    source_revision_after_decision=bool(
                        _revision_timing(tape, opening_key, prefinal.receive_ms)
                        == "later"
                        or _revision_timing(tape, prefinal_key, prefinal.receive_ms)
                        == "later"
                    ),
                )
            )
        else:
            audit["prefinal_received_too_late"] += 1
        final = tape.points.get(final_key)
        if final is None:
            audit[
                "ambiguous_final"
                if final_key in tape.ambiguous_payload_ms
                else "missing_final"
            ] += 1
            continue
        if opening.receive_ms > final.receive_ms:
            audit["opening_received_after_final_proof"] += 1
            continue
        opening_revision = _revision_timing(tape, opening_key, final.receive_ms)
        final_revision = _revision_timing(tape, final_key, final.receive_ms)
        if opening_revision == "known":
            audit["ambiguous_opening"] += 1
            continue
        if final_revision == "known":
            audit["ambiguous_final"] += 1
            continue
        if final.receive_ms + config.local_path_ms >= close_ms:
            audit["final_received_too_late_to_submit"] += 1
            continue
        revised_later = opening_revision == "later" or final_revision == "later"
        if revised_later:
            audit["post_decision_revisions"] += 1
        direct_winner = _winner(final.value, opening.value)
        official = official_outcomes.get(market_id)
        if official in {"Up", "Down"}:
            audit[
                "official_matches"
                if official == direct_winner
                else "official_mismatches"
            ] += 1
        else:
            audit["unresolved_official"] += 1
            official = None
        output[FINAL_VARIANT].append(
            _signal(
                FINAL_VARIANT,
                market_id,
                slot,
                opening,
                final,
                direct_winner,
                official,
                source_revision_after_decision=revised_later,
            )
        )
        output[WRONG_SIDE_VARIANT].append(
            _signal(
                WRONG_SIDE_VARIANT,
                market_id,
                slot,
                opening,
                final,
                "Down" if direct_winner == "Up" else "Up",
                official,
                source_revision_after_decision=revised_later,
            )
        )
    for key in (
        "markets",
        "ambiguous_opening",
        "missing_opening",
        "ambiguous_prefinal",
        "missing_prefinal",
        "prefinal_received_too_late",
        "ambiguous_final",
        "missing_final",
        "final_received_too_late_to_submit",
        "official_matches",
        "official_mismatches",
        "unresolved_official",
        "opening_received_after_prefinal_proof",
        "opening_received_after_final_proof",
        "post_decision_revisions",
    ):
        audit.setdefault(key, 0)
    return output, dict(audit)


def execution_config(config: StrategyConfig | None = None) -> h_replay.ReplayConfig:
    config = config or StrategyConfig()
    replay = h_replay.ReplayConfig(
        book_lag_ms=0.0,
        local_path_ms=config.local_path_ms,
        order_wire_ms=0.0,
        venue_hold_ms=config.venue_hold_ms,
        evaluation_ms=config.evaluation_ms,
        theta=config.theta,
        tick=config.tick,
        fee_rate=config.fee_rate,
        base_shares=config.base_shares,
        reentry_min_ms=2_000.0,
        reverse_multiplier=1.0,
        pilot_min_usd=config.pilot_min_usd,
        max_order_usd=config.max_order_usd,
        signal_time_basis="local_receipt_timestamp",
        retry_after_no_send_or_kill=False,
        reentry_enabled=False,
        max_orders_per_min=0,
    )
    fixed_limit = h_replay.limit_price(1.0, replay.theta, replay.tick, replay.fee_rate)
    if not math.isclose(fixed_limit or math.nan, 0.97, abs_tol=1e-12):
        raise AssertionError(f"settlement-lag limit drifted from 0.97 to {fixed_limit}")
    if config.primary_evaluation_ms not in config.evaluation_ms:
        raise ValueError("primary execution horizon is not evaluated")
    return replay


def _execution_paths(
    market: h_replay_run.DirectMarketBook,
    direction: str,
) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    """Return independent native and complementary execution paths.

    The two books can describe the same economic liquidity.  They are alternatives,
    not levels that may be added to manufacture a five-share fill.
    """
    own = market.up if direction == "Up" else market.down
    opposite = market.down if direction == "Up" else market.up
    paths: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    if own.ready:
        paths.append(
            (
                "native_ask",
                tuple(
                    sorted(
                        (
                            (market._key(price) / 10_000.0, float(size))
                            for price, size in own.asks.items()
                            if float(size) > 0
                        ),
                        key=lambda level: level[0],
                    )
                ),
            )
        )
    if opposite.ready:
        paths.append(
            (
                "complementary_bid",
                tuple(
                    sorted(
                        (
                            (
                                (10_000 - market._key(price)) / 10_000.0,
                                float(size),
                            )
                            for price, size in opposite.bids.items()
                            if float(size) > 0
                        ),
                        key=lambda level: level[0],
                    )
                ),
            )
        )
    return tuple(paths)


def _path_fill_metrics(
    levels: Sequence[tuple[float, float]],
    fixed_limit: float,
    target_shares: float,
    fee_rate: float,
) -> tuple[float, float]:
    remaining = target_shares
    shares = 0.0
    cost = 0.0
    for price, available in levels:
        if price > fixed_limit + 1e-9 or remaining <= 1e-12:
            break
        consumed = min(float(available), remaining)
        shares += consumed
        cost += consumed * (price + h_replay.taker_fee(price, fee_rate))
        remaining -= consumed
    return shares, cost / shares if shares > 0 else math.inf


def _truncate_path(
    levels: Sequence[tuple[float, float]],
    fixed_limit: float,
    target_shares: float,
) -> tuple[tuple[float, float], ...]:
    """Cap eligible displayed liquidity at the exact submitted share target."""
    remaining = target_shares
    output: list[tuple[float, float]] = []
    for price, available in levels:
        size = float(available)
        if price <= fixed_limit + 1e-9:
            size = min(size, max(remaining, 0.0))
            remaining -= size
        if size > 1e-12:
            output.append((float(price), size))
    return tuple(output)


def _select_execution_path(
    market: h_replay_run.DirectMarketBook,
    direction: str,
    *,
    fixed_limit: float,
    target_shares: float,
    fee_rate: float,
) -> tuple[str, tuple[tuple[float, float], ...]] | None:
    paths = _execution_paths(market, direction)
    if not paths:
        return None

    def score(path: tuple[str, tuple[tuple[float, float], ...]]) -> tuple[Any, ...]:
        name, levels = path
        shares, all_in = _path_fill_metrics(
            levels, fixed_limit, target_shares, fee_rate
        )
        top = levels[0][0] if levels else math.inf
        # Capacity is taken from one path only.  For equal capacity, retaining the
        # more expensive route is conservative for PnL and deterministic.
        return (shares, all_in, bool(levels), top, name == "native_ask")

    name, levels = max(paths, key=score)
    return name, _truncate_path(levels, fixed_limit, target_shares)


def _proven_effective_asks(
    market: h_replay_run.DirectMarketBook,
    direction: str,
    *,
    fixed_limit: float = 0.97,
    target_shares: float = 5.0,
    fee_rate: float = 0.07,
) -> tuple[tuple[float, float], ...] | None:
    """Return exactly one proven execution path, never a sum of alternatives."""
    selected = _select_execution_path(
        market,
        direction,
        fixed_limit=fixed_limit,
        target_shares=target_shares,
        fee_rate=fee_rate,
    )
    return None if selected is None else selected[1]


@dataclass
class _ReceiptTokenBook:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    bids_ns: dict[float, int | None] = field(default_factory=dict)
    asks_ns: dict[float, int | None] = field(default_factory=dict)

    def replace(
        self,
        bids: Iterable[Mapping[str, Any]],
        asks: Iterable[Mapping[str, Any]],
        receive_ms: float,
        receive_ns: int | None,
    ) -> None:
        bids = tuple(bids)
        asks = tuple(asks)
        self.bids = {
            float(level["price"]): receive_ms
            for level in bids
            if float(level["size"]) > 0
        }
        self.asks = {
            float(level["price"]): receive_ms
            for level in asks
            if float(level["size"]) > 0
        }
        self.bids_ns = {
            float(level["price"]): receive_ns
            for level in bids
            if float(level["size"]) > 0
        }
        self.asks_ns = {
            float(level["price"]): receive_ns
            for level in asks
            if float(level["size"]) > 0
        }

    def sync_after_change(
        self,
        before_bids: Mapping[float, float],
        before_asks: Mapping[float, float],
        token: h_replay_run.TokenBook,
        receive_ms: float,
        receive_ns: int | None,
    ) -> None:
        previous_bids_ns = dict(self.bids_ns)
        previous_asks_ns = dict(self.asks_ns)
        self.bids = {
            price: (
                self.bids[price]
                if before_bids.get(price) == size and price in self.bids
                else receive_ms
            )
            for price, size in token.bids.items()
        }
        self.asks = {
            price: (
                self.asks[price]
                if before_asks.get(price) == size and price in self.asks
                else receive_ms
            )
            for price, size in token.asks.items()
        }
        self.bids_ns = {
            price: (
                previous_bids_ns.get(price)
                if before_bids.get(price) == size and price in previous_bids_ns
                else receive_ns
            )
            for price, size in token.bids.items()
        }
        self.asks_ns = {
            price: (
                previous_asks_ns.get(price)
                if before_asks.get(price) == size and price in previous_asks_ns
                else receive_ns
            )
            for price, size in token.asks.items()
        }


@dataclass
class _ReceiptMarketBook:
    up: _ReceiptTokenBook = field(default_factory=_ReceiptTokenBook)
    down: _ReceiptTokenBook = field(default_factory=_ReceiptTokenBook)


def _effective_asks_with_receipts(
    market: h_replay_run.DirectMarketBook,
    receipts: _ReceiptMarketBook,
    direction: str,
    *,
    fixed_limit: float = 0.97,
    target_shares: float = 5.0,
    fee_rate: float = 0.07,
) -> tuple[tuple[float, float, float, int | None], ...] | None:
    own = market.up if direction == "Up" else market.down
    opposite = market.down if direction == "Up" else market.up
    own_receipts = receipts.up if direction == "Up" else receipts.down
    opposite_receipts = receipts.down if direction == "Up" else receipts.up
    selected = _select_execution_path(
        market,
        direction,
        fixed_limit=fixed_limit,
        target_shares=target_shares,
        fee_rate=fee_rate,
    )
    if selected is None:
        return None
    path_name, selected_levels = selected
    receipt_by_effective_price: dict[int, float] = {}
    receipt_ns_by_effective_price: dict[int, int | None] = {}
    if path_name == "native_ask":
        for price in own.asks:
            receipt = own_receipts.asks.get(price)
            if receipt is None:
                return None
            key = market._key(price)
            receipt_by_effective_price[key] = receipt
            receipt_ns_by_effective_price[key] = own_receipts.asks_ns.get(price)
    else:
        for price in opposite.bids:
            receipt = opposite_receipts.bids.get(price)
            if receipt is None:
                return None
            key = 10_000 - market._key(price)
            receipt_by_effective_price[key] = receipt
            receipt_ns_by_effective_price[key] = opposite_receipts.bids_ns.get(price)
    return tuple(
        (
            price,
            size,
            receipt_by_effective_price[int(round(price * 10_000))],
            receipt_ns_by_effective_price[int(round(price * 10_000))],
        )
        for price, size in selected_levels
    )


def _level_receipt_audit(
    market: h_replay_run.DirectMarketBook | None,
    receipts: _ReceiptMarketBook | None,
    direction: str,
    evaluation_ms: float,
    filled_shares: float,
    fixed_limit: float | None,
    *,
    target_shares: float = 5.0,
    fee_rate: float = 0.07,
) -> dict[str, Any]:
    selected = (
        _select_execution_path(
            market,
            direction,
            fixed_limit=fixed_limit,
            target_shares=target_shares,
            fee_rate=fee_rate,
        )
        if market is not None and fixed_limit is not None
        else None
    )
    levels = (
        _effective_asks_with_receipts(
            market,
            receipts,
            direction,
            fixed_limit=fixed_limit if fixed_limit is not None else 0.97,
            target_shares=target_shares,
            fee_rate=fee_rate,
        )
        if market is not None and receipts is not None and fixed_limit is not None
        else None
    )
    if levels is None:
        return {
            "effective_execution_path": None,
            "effective_top_ask_receive_ms": None,
            "effective_fill_oldest_receive_ms": None,
            "effective_fill_newest_receive_ms": None,
            "effective_fill_max_age_ms": None,
            "effective_top_ask_receive_ns": None,
            "effective_fill_oldest_receive_ns": None,
            "effective_fill_newest_receive_ns": None,
            "book_receipt_provenance_exact": False,
            "book_receipt_nanoseconds_exact": False,
        }
    top_receipt = levels[0][2] if levels else None
    top_receipt_ns = levels[0][3] if levels else None
    remaining = max(float(filled_shares), 0.0)
    used_receipts: list[float] = []
    used_receipts_ns: list[int] = []
    all_used_receipts_have_ns = True
    if remaining > 0 and fixed_limit is not None:
        for price, size, receipt, receipt_ns in levels:
            if price > fixed_limit + 1e-9 or remaining <= 1e-12:
                break
            consumed = min(size, remaining)
            if consumed > 0:
                used_receipts.append(receipt)
                if receipt_ns is None:
                    all_used_receipts_have_ns = False
                else:
                    used_receipts_ns.append(receipt_ns)
                remaining -= consumed
    exact = remaining <= 1e-9 if filled_shares > 0 else True
    oldest = min(used_receipts) if used_receipts else None
    newest = max(used_receipts) if used_receipts else None
    oldest_ns = min(used_receipts_ns) if used_receipts_ns else None
    newest_ns = max(used_receipts_ns) if used_receipts_ns else None
    return {
        "effective_execution_path": selected[0] if selected is not None else None,
        "effective_top_ask_receive_ms": top_receipt,
        "effective_fill_oldest_receive_ms": oldest,
        "effective_fill_newest_receive_ms": newest,
        "effective_fill_max_age_ms": evaluation_ms - oldest
        if oldest is not None
        else None,
        "book_receipt_provenance_exact": exact,
        "effective_top_ask_receive_ns": top_receipt_ns,
        "effective_fill_oldest_receive_ns": oldest_ns,
        "effective_fill_newest_receive_ns": newest_ns,
        "book_receipt_nanoseconds_exact": bool(
            exact
            and (filled_shares <= 0 or all_used_receipts_have_ns)
            and (filled_shares <= 0 or len(used_receipts_ns) == len(used_receipts))
        ),
    }


def _exact_fill_rejection_reason(
    signal: SettlementSignal,
    evaluation_ms: float,
    audit: Mapping[str, Any],
) -> str | None:
    """Conservatively gate a float-clock replay with the original integer clocks."""
    if signal.decision_ns is None:
        return None
    newest_ns = audit.get("effective_fill_newest_receive_ns")
    if audit.get("book_receipt_nanoseconds_exact") is not True or newest_ns is None:
        return "exact_receipt_provenance_missing"
    deadline_ns = signal.decision_ns + int(round(evaluation_ms * 1_000_000.0))
    if int(newest_ns) > deadline_ns:
        return "exact_receipt_after_evaluation"
    return None


def _publish_market(
    machine: h_replay.HReplay,
    market: h_replay_run.DirectMarketBook,
    receive_ms: float,
    *,
    required_direction: str | None = None,
    fixed_limit: float = 0.97,
    target_shares: float = 5.0,
    fee_rate: float = 0.07,
) -> None:
    up = _proven_effective_asks(
        market,
        "Up",
        fixed_limit=fixed_limit,
        target_shares=target_shares,
        fee_rate=fee_rate,
    )
    down = _proven_effective_asks(
        market,
        "Down",
        fixed_limit=fixed_limit,
        target_shares=target_shares,
        fee_rate=fee_rate,
    )
    required = up if required_direction == "Up" else down
    if required_direction is not None and required is None:
        machine.feed(
            {
                "kind": "invalidate",
                "market_id": market.market_id,
                "receive_ms": receive_ms,
                "source_ms": receive_ms,
                "advance_watermark": False,
            }
        )
        return
    if up is None and down is None:
        machine.feed(
            {
                "kind": "invalidate",
                "market_id": market.market_id,
                "receive_ms": receive_ms,
                "source_ms": receive_ms,
                "advance_watermark": False,
            }
        )
        return
    machine.feed(
        {
            "kind": "snapshot",
            "market_id": market.market_id,
            "receive_ms": receive_ms,
            "source_ms": receive_ms,
            "up_asks": up or (),
            "down_asks": down or (),
            "levels_normalized": True,
            "advance_watermark": False,
        }
    )


def _venue_fee(raw_fee: float) -> float:
    return float(
        Decimal(str(raw_fee)).quantize(Decimal("0.00001"), rounding=ROUND_DOWN)
    )


def apply_settlement_costs(
    rows: Mapping[str, Sequence[dict[str, Any]]],
    result_available_ms: Mapping[str, float],
    config: StrategyConfig,
) -> None:
    seconds_per_year = 365.25 * 86_400.0
    for variant_rows in rows.values():
        for row in variant_rows:
            shares = float(row.get("filled_shares") or 0.0)
            won = row.get("won")
            all_in_cost = row.get("all_in_cost")
            if shares <= 0 or won is None or all_in_cost is None:
                row.update(
                    {
                        "gross_pnl": None,
                        "capital_lock_seconds": 0.0,
                        "capital_lock_cost": 0.0,
                        "redemption_cost": 0.0,
                    }
                )
                continue
            result_time = result_available_ms.get(str(row["market_id"]))
            if result_time is None or not math.isfinite(float(result_time)):
                row.update(
                    {
                        "gross_pnl": None,
                        "capital_lock_seconds": None,
                        "capital_lock_cost": None,
                        "redemption_cost": None,
                        "pnl_per_share": None,
                        "pnl": None,
                    }
                )
                continue
            gross = ((1.0 if bool(won) else 0.0) - float(all_in_cost)) * shares
            lock_seconds = max(
                0.0,
                (float(result_time) - float(row["evaluation_time_ms"])) / 1_000.0,
            )
            committed = float(all_in_cost) * shares
            capital_cost = (
                committed * config.capital_annual_rate * lock_seconds / seconds_per_year
            )
            redemption_cost = config.redemption_cost_usd
            net = gross - capital_cost - redemption_cost
            row.update(
                {
                    "gross_pnl": gross,
                    "capital_lock_seconds": lock_seconds,
                    "capital_lock_cost": capital_cost,
                    "redemption_cost": redemption_cost,
                    "pnl_per_share": net / shares,
                    "pnl": net,
                }
            )


def replay_execution(
    signals: Mapping[str, Sequence[SettlementSignal]],
    clob_events: Iterable[dict[str, Any]],
    mappings: Iterable[dict[str, Any]],
    config: StrategyConfig | None = None,
) -> dict[str, list[dict[str, Any]]]:
    strategy = config or StrategyConfig()
    replay_config = execution_config(strategy)
    fixed_limit = h_replay.limit_price(
        1.0,
        replay_config.theta,
        replay_config.tick,
        replay_config.fee_rate,
    )
    if fixed_limit is None:
        raise AssertionError("settlement-lag fixed limit is unavailable")
    markets_by_slot, token_index = h_replay_run._mapping_dict(mappings)
    markets_by_id = {market.market_id: market for market in markets_by_slot.values()}
    receipt_markets = {market_id: _ReceiptMarketBook() for market_id in markets_by_id}
    machines = {variant: h_replay.HReplay(replay_config) for variant in signals}
    signal_lookup: dict[tuple[str, str], SettlementSignal] = {}
    decision_book_state: dict[tuple[str, str], str] = {}
    decision_receipts: dict[tuple[str, str], dict[str, Any]] = {}

    def annotate_new_records(machine: h_replay.HReplay, start: int) -> None:
        for row in machine.records[start:]:
            market_id = str(row["market_id"])
            row.update(
                _level_receipt_audit(
                    markets_by_id.get(market_id),
                    receipt_markets.get(market_id),
                    str(row["direction"]),
                    float(row["evaluation_time_ms"]),
                    float(row.get("filled_shares") or 0.0),
                    float(row["fixed_limit"])
                    if row.get("fixed_limit") is not None
                    else None,
                    target_shares=strategy.base_shares,
                    fee_rate=strategy.fee_rate,
                )
            )

    ordered_signals = []
    sequence = 0
    for variant, variant_signals in signals.items():
        for signal in variant_signals:
            signal_lookup[(variant, signal.market_id)] = signal
            decision_order_ns = (
                signal.decision_ns
                if signal.decision_ns is not None
                else int(round(signal.decision_ms * 1_000_000.0))
            )
            ordered_signals.append(
                (decision_order_ns, 0, sequence, variant, signal)
            )
            sequence += 1
    ordered_signals.sort()
    batches = (
        (
            int(batch["recv_ns"]),
            1,
            int(batch["seq"]),
            "",
            batch,
        )
        for batch in _clob_batches_exact(clob_events)
    )
    connected = False
    active_connection_epoch: int | None = None
    for _receipt_order_ns, kind_order, _, variant, payload in heapq.merge(
        ordered_signals, batches
    ):
        if kind_order == 0:
            signal = cast(SettlementSignal, payload)
            receive_ms = signal.decision_ms
            machine = machines[variant]
            record_start = len(machine.records)
            machine._drain(signal.decision_ms, inclusive=False)
            annotate_new_records(machine, record_start)
            market = markets_by_id.get(signal.market_id)
            if connected and market is not None:
                decision_levels = _proven_effective_asks(
                    market,
                    signal.direction,
                    fixed_limit=fixed_limit,
                    target_shares=strategy.base_shares,
                    fee_rate=strategy.fee_rate,
                )
                decision_book_state[(variant, signal.market_id)] = (
                    "unavailable"
                    if decision_levels is None
                    else "empty"
                    if not decision_levels
                    else "available"
                )
                decision_receipts[(variant, signal.market_id)] = _level_receipt_audit(
                    market,
                    receipt_markets[signal.market_id],
                    signal.direction,
                    signal.decision_ms,
                    0.0,
                    fixed_limit,
                    target_shares=strategy.base_shares,
                    fee_rate=strategy.fee_rate,
                )
                _publish_market(
                    machine,
                    market,
                    signal.decision_ms,
                    required_direction=signal.direction,
                    fixed_limit=fixed_limit,
                    target_shares=strategy.base_shares,
                    fee_rate=strategy.fee_rate,
                )
            else:
                decision_book_state[(variant, signal.market_id)] = "unavailable"
                decision_receipts[(variant, signal.market_id)] = _level_receipt_audit(
                    None,
                    None,
                    signal.direction,
                    signal.decision_ms,
                    0.0,
                    fixed_limit,
                    target_shares=strategy.base_shares,
                    fee_rate=strategy.fee_rate,
                )
                machine.feed(
                    {
                        "kind": "invalidate",
                        "market_id": signal.market_id,
                        "receive_ms": signal.decision_ms,
                        "source_ms": signal.decision_ms,
                        "advance_watermark": False,
                    }
                )
            machine.feed(
                {
                    "kind": "signal",
                    "market_id": signal.market_id,
                    "receive_ms": signal.decision_ms,
                    "source_ms": signal.decision_ms,
                    "signal_source": signal.variant,
                    "direction": signal.direction,
                    "fair": 1.0,
                    "trial_edge": signal.margin,
                    "trigger_reason": "twap60_final_proof",
                }
            )
            continue

        batch = cast(dict[str, Any], payload)
        receive_ms = float(batch["recv_ms"])
        batch_receive_ns = int(batch["recv_ns"])
        for machine in machines.values():
            if machine.has_pending_evaluations():
                record_start = len(machine.records)
                machine._drain(receive_ms, inclusive=False)
                annotate_new_records(machine, record_start)
        touched: dict[str, h_replay_run.DirectMarketBook] = {}
        lifecycle: list[str] = []
        for event in batch["events"]:
            event_kind = event["kind"]
            event_epoch = int(event.get("connection_epoch") or 0)
            if event_kind == "clob_error":
                if (
                    active_connection_epoch is not None
                    and event_epoch != active_connection_epoch
                ):
                    continue
                for market, _ in token_index.values():
                    market.up = h_replay_run.TokenBook()
                    market.down = h_replay_run.TokenBook()
                    receipt_markets[market.market_id] = _ReceiptMarketBook()
                connected = False
                active_connection_epoch = None
                lifecycle.append("disconnect")
                continue
            if event_kind == "clob_connection":
                if (
                    active_connection_epoch is not None
                    and event_epoch < active_connection_epoch
                ):
                    continue
                active_connection_epoch = event_epoch
                connected = True
                lifecycle.append("reconnect")
                continue
            if not connected or event_epoch != active_connection_epoch:
                continue
            hit = token_index.get(str(event.get("asset_id")))
            if hit is None:
                continue
            market, direction = hit
            token = market.up if direction == "Up" else market.down
            receipt_token = (
                receipt_markets[market.market_id].up
                if direction == "Up"
                else receipt_markets[market.market_id].down
            )
            source_ms = float(event["source_ts_ms"])
            applied = False
            if event_kind == "clob_snapshot" and source_ms >= token.source_ms:
                token.replace(event["bids"], event["asks"], source_ms)
                receipt_token.replace(
                    event["bids"],
                    event["asks"],
                    receive_ms,
                    batch_receive_ns,
                )
                applied = True
            elif event_kind == "clob_price_change":
                before_bids = dict(token.bids)
                before_asks = dict(token.asks)
                applied = token.change(
                    str(event["side"]),
                    float(event["price"]),
                    float(event["size"]),
                    source_ms,
                    event.get("best_bid"),
                    event.get("best_ask"),
                )
                if applied:
                    receipt_token.sync_after_change(
                        before_bids,
                        before_asks,
                        token,
                        receive_ms,
                        batch_receive_ns,
                    )
            if applied:
                touched[market.market_id] = market
        for machine in machines.values():
            for lifecycle_kind in lifecycle:
                machine.feed({"kind": lifecycle_kind, "receive_ms": receive_ms})
            if not machine.has_pending_evaluations():
                continue
            for market in touched.values():
                _publish_market(
                    machine,
                    market,
                    receive_ms,
                    fixed_limit=fixed_limit,
                    target_shares=strategy.base_shares,
                    fee_rate=strategy.fee_rate,
                )
            record_start = len(machine.records)
            machine.feed(
                {"kind": "watermark", "receive_ms": receive_ms, "source_ms": receive_ms}
            )
            annotate_new_records(machine, record_start)

    output: dict[str, list[dict[str, Any]]] = {}
    for variant, machine in machines.items():
        record_start = len(machine.records)
        machine.finish(force=False)
        annotate_new_records(machine, record_start)
        records = list(machine.records)
        records.extend(
            {
                **pending,
                "filled_shares": 0.0,
                "fill_price": None,
                "fill_fee": 0.0,
                "all_in_cost": None,
                "reason": "censored_end_of_recording",
            }
            for pending in machine.pending_evaluations()
        )
        enriched = []
        for row in records:
            signal = signal_lookup[(variant, str(row["market_id"]))]
            row["variant"] = variant
            row["strategy_id"] = "twap60-final-race-v3"
            row["family"] = 37
            row["proof_payload_ms"] = signal.proof_payload_ms
            row["proof_push_ms"] = signal.proof_push_ms
            row["proof_lead_ms"] = signal.proof_lead_ms
            row["proof_opening"] = signal.opening
            row["proof_closing"] = signal.closing
            row["proof_margin"] = signal.margin
            row["source_revision_after_decision"] = (
                signal.source_revision_after_decision
            )
            for name, value in _level_receipt_audit(
                markets_by_id.get(signal.market_id),
                receipt_markets.get(signal.market_id),
                signal.direction,
                float(row["evaluation_time_ms"]),
                float(row.get("filled_shares") or 0.0),
                float(row["fixed_limit"])
                if row.get("fixed_limit") is not None
                else None,
                target_shares=strategy.base_shares,
                fee_rate=strategy.fee_rate,
            ).items():
                row.setdefault(name, value)
            decision_receipt = decision_receipts.get((variant, signal.market_id), {})
            row["decision_execution_path"] = decision_receipt.get(
                "effective_execution_path"
            )
            row["decision_effective_ask_receive_ms"] = decision_receipt.get(
                "effective_top_ask_receive_ms"
            )
            row["decision_effective_ask_age_ms"] = (
                signal.decision_ms - float(row["decision_effective_ask_receive_ms"])
                if row["decision_effective_ask_receive_ms"] is not None
                else None
            )
            if (
                decision_book_state.get((variant, signal.market_id)) == "empty"
                and row.get("decision_reason") == "book_unavailable_at_decision"
            ):
                row["decision_reason"] = "no_effective_ask_at_decision"
                if row.get("reason") == "book_unavailable_at_decision":
                    row["reason"] = "no_effective_ask_at_decision"
            if float(row.get("filled_shares") or 0.0) > 0:
                if float(row["filled_shares"]) > strategy.base_shares + 1e-9:
                    raise AssertionError("settlement-lag fill exceeded the five-share cap")
                exact_rejection = _exact_fill_rejection_reason(
                    signal,
                    float(row["evaluation_ms"]),
                    row,
                )
                if exact_rejection is not None:
                    row.update(
                        {
                            "filled_shares": 0.0,
                            "fill_price": None,
                            "fill_fee": 0.0,
                            "all_in_cost": None,
                            "fill_fee_per_share": None,
                            "eligible_depth": 0.0,
                            "match_price": None,
                            "filled": False,
                            "reason": exact_rejection,
                        }
                    )
            if float(row.get("filled_shares") or 0.0) > 0:
                row["fill_fee"] = _venue_fee(float(row["fill_fee"]))
                row["fill_fee_per_share"] = float(row["fill_fee"]) / float(
                    row["filled_shares"]
                )
                row["all_in_cost"] = (
                    float(row["fill_price"]) + row["fill_fee_per_share"]
                )
            market_close_ms = (signal.slot + 300) * 1_000.0
            crossed_close = (
                signal.decision_ms + float(row["evaluation_ms"]) >= market_close_ms
            )
            raw_cross_close = None
            if crossed_close:
                raw_cross_close = {
                    "reason": row["reason"],
                    "filled_shares": float(row["filled_shares"]),
                    "fill_price": row["fill_price"],
                    "fill_fee": float(row["fill_fee"]),
                    "all_in_cost": row["all_in_cost"],
                }
            row["winner"] = signal.actual_winner
            row["won"] = (
                row["direction"] == signal.actual_winner
                if signal.actual_winner is not None
                else None
            )
            if raw_cross_close is not None:
                raw_shares = float(raw_cross_close["filled_shares"])
                raw_cost = raw_cross_close["all_in_cost"]
                raw_cross_close["pnl"] = (
                    ((1.0 if row["won"] else 0.0) - float(raw_cost)) * raw_shares
                    if raw_shares > 0
                    and raw_cost is not None
                    and row["won"] is not None
                    else None
                )
                row["cross_close_sensitivity"] = raw_cross_close
                row.update(
                    {
                        "filled_shares": 0.0,
                        "fill_price": None,
                        "fill_fee": 0.0,
                        "fill_fee_per_share": None,
                        "all_in_cost": None,
                        "eligible_depth": 0.0,
                        "match_price": None,
                        "filled": False,
                        "reason": "after_market_close",
                    }
                )
            else:
                row["cross_close_sensitivity"] = None
            row["match_book_clock"] = "receipt_ms"
            row["match_level_clock"] = "original_local_receipt_ms"
            row["protocol_slot"] = signal.slot
            row["market_close_ms"] = market_close_ms
            row["day"] = (
                datetime.fromtimestamp(
                    signal.decision_ms / 1_000.0,
                    UTC,
                )
                .date()
                .isoformat()
            )
            row["qualified_fill"] = bool(
                float(row["filled_shares"]) >= strategy.base_shares - 1e-9
                and row["reason"] == "filled"
            )
            if float(row["filled_shares"]) > 0 and row["won"] is not None:
                row["pnl_per_share"] = (1.0 if row["won"] else 0.0) - float(
                    row["all_in_cost"]
                )
                row["pnl"] = row["pnl_per_share"] * float(row["filled_shares"])
            else:
                row["pnl_per_share"] = None
                row["pnl"] = None
            enriched.append(row)
        output[variant] = enriched
    apply_settlement_costs(
        output,
        {
            market_id: (float(market.slot) + 300.0) * 1_000.0
            for market_id, market in markets_by_id.items()
        },
        strategy,
    )
    return output


def _daily_lower_99(rows: Sequence[Mapping[str, Any]]) -> float | None:
    return settlement_proxy._daily_lower_99(rows)


def summarize_execution(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    signals: Mapping[str, Sequence[SettlementSignal]],
    *,
    horizons: Sequence[float] | None = None,
    config: StrategyConfig | None = None,
) -> dict[str, dict[str, Any]]:
    config = config or StrategyConfig()
    horizons = tuple(config.evaluation_ms if horizons is None else horizons)
    output: dict[str, dict[str, Any]] = {}
    for variant, variant_signals in signals.items():
        by_horizon: dict[str, Any] = {}
        opportunity_count = len(variant_signals)
        opportunity_days = {
            datetime.fromtimestamp(signal.decision_ms / 1_000.0, UTC).date().isoformat()
            for signal in variant_signals
        }
        labelled = [
            signal for signal in variant_signals if signal.actual_winner is not None
        ]
        for horizon in horizons:
            sample = [
                row
                for row in rows.get(variant, ())
                if math.isclose(
                    float(row["evaluation_ms"]), float(horizon), abs_tol=1e-9
                )
            ]
            executions = [
                row
                for row in sample
                if float(row.get("filled_shares") or 0.0) > 0
                and row.get("winner") is not None
            ]
            full_fills = [row for row in executions if bool(row.get("qualified_fill"))]
            cross_close = [
                row["cross_close_sensitivity"]
                for row in sample
                if isinstance(row.get("cross_close_sensitivity"), Mapping)
                and float(row["cross_close_sensitivity"].get("filled_shares") or 0.0)
                > 0
                and row["cross_close_sensitivity"].get("pnl") is not None
            ]
            total_shares = sum(float(row["filled_shares"]) for row in executions)
            total_pnl = sum(float(row["pnl"]) for row in executions)
            cross_close_shares = sum(float(row["filled_shares"]) for row in cross_close)
            cross_close_pnl = sum(float(row["pnl"]) for row in cross_close)
            positive_by_market: dict[str, float] = {}
            for row in executions:
                market_id = str(row["market_id"])
                positive_by_market[market_id] = positive_by_market.get(
                    market_id, 0.0
                ) + max(float(row["pnl"]), 0.0)
            positive_total = sum(positive_by_market.values())
            raw_p = h_replay_run._exact_pvalue(full_fills)
            decision_asks = [
                float(row["decision_ask"])
                for row in sample
                if row.get("decision_ask") is not None
            ]
            by_horizon[str(int(horizon))] = {
                "opportunities": opportunity_count,
                "opportunity_days": len(opportunity_days),
                "sends": sum(bool(row.get("sent")) for row in sample),
                "fills": len(full_fills),
                "partial_fills": sum(
                    0 < float(row["filled_shares"]) < config.base_shares - 1e-9
                    for row in executions
                ),
                "fill_rate": len(full_fills) / opportunity_count
                if opportunity_count
                else 0.0,
                "shares": total_shares,
                "pnl": total_pnl,
                "net_cents_per_share": 100.0 * total_pnl / total_shares
                if total_shares
                else None,
                "pnl_per_opportunity": total_pnl / opportunity_count
                if opportunity_count
                else 0.0,
                "dollars_per_day": total_pnl / len(opportunity_days)
                if opportunity_days
                else 0.0,
                "direction_accuracy": sum(
                    signal.direction == signal.actual_winner for signal in labelled
                )
                / len(labelled)
                if labelled
                else None,
                "day_cluster_lower_99": _daily_lower_99(executions),
                "raw_exact_p": raw_p,
                "exact_test_full_fill_count": len(full_fills),
                "multiplicity_adjusted_p": min(1.0, raw_p * config.family_tests),
                "top_market_positive_pnl_share": max(
                    positive_by_market.values(), default=0.0
                )
                / positive_total
                if positive_total
                else None,
                "reasons": dict(Counter(str(row.get("reason")) for row in sample)),
                "decision_reasons": dict(
                    Counter(str(row.get("decision_reason")) for row in sample)
                ),
                "decision_ask": _quantiles(decision_asks),
                # Displayed L2 cannot prove queue priority against hidden competing FAKs.
                "controllable_capacity_lower_bound_shares": 0.0,
                "displayed_full_target_shares": len(full_fills) * config.base_shares,
                "executed_shares": total_shares,
                "cross_close_sensitivity": {
                    "executions": len(cross_close),
                    "shares": cross_close_shares,
                    "pnl": cross_close_pnl,
                    "net_cents_per_share": 100.0 * cross_close_pnl / cross_close_shares
                    if cross_close_shares
                    else None,
                },
            }
        output[variant] = by_horizon
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_file_identity(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    return {
        "name": resolved.name,
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _standard_artifact_root(path: str | Path) -> Path:
    root = Path(path).resolve()
    nested = root / "strict"
    return nested if (nested / "manifest.json").is_file() else root


def _standard_artifact_identity(path: str | Path) -> dict[str, Any]:
    root = _standard_artifact_root(path)
    try:
        checked = h_replay_archive.validate_standard_artifact(root)
        manifest = checked["manifest"]
        files = {
            name: _portable_file_identity(root / name)
            for name in STANDARD_ARTIFACT_FILES
        }
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ValueError("standard artifact identity drift") from exc
    return {
        "run_id": manifest.get("run_id"),
        "collector_region": manifest.get("collector_region"),
        "receipt_race_ready": manifest.get("receipt_race_ready") is True,
        "started_ms": manifest.get("started_ms"),
        "ended_ms": manifest.get("ended_ms"),
        "files": files,
    }


def clock_attestation_payload(
    twap_paths: Sequence[str | Path],
    artifact_path: str | Path,
    metadata_paths: Sequence[str | Path],
    *,
    machine_id_sha256: str,
    boot_id: str,
    ntp_synchronized: bool,
    clob_custody: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the three receipt-clock inputs to one synchronized collector boot."""
    _validate_clob_custody(clob_custody)
    return {
        "schema": CLOCK_ATTESTATION_SCHEMA,
        "collector_region": "eu-west-1",
        "clock_domain": INPUT_CLOCK_DOMAIN,
        "machine_id_sha256": str(machine_id_sha256),
        "boot_id": str(boot_id),
        "ntp_synchronized": bool(ntp_synchronized),
        "clob_custody": dict(clob_custody),
        "inputs": {
            "twap": [
                _portable_file_identity(path)
                for path in sorted(Path(value) for value in twap_paths)
            ],
            "strict_artifact": _standard_artifact_identity(artifact_path),
            "execution_metadata": [
                _portable_file_identity(path)
                for path in sorted(Path(value) for value in metadata_paths)
            ],
        },
    }


def _validate_portable_identity(value: object, *, expected_name: str | None) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("invalid settlement-lag CLOB custody identity")
    name = value.get("name")
    byte_count = value.get("bytes")
    digest = value.get("sha256")
    if (
        not isinstance(name, str)
        or not name
        or (expected_name is not None and name != expected_name)
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count <= 0
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise ValueError("invalid settlement-lag CLOB custody identity")


def _validate_clob_custody(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "scope",
        "hours",
        "active_witnesses",
        "closed_identities",
        "sidecar",
        "archive_snapshot",
        "strict_input_binding",
        "raw_archive_manifests_sha256",
    }:
        raise ValueError("invalid settlement-lag CLOB custody attestation")
    sidecar = value.get("sidecar")
    if (
        value.get("scope") != CLOB_CUSTODY_SCOPE
        or value.get("hours") != 24
        or value.get("active_witnesses") != 24
        or value.get("closed_identities") != 24
        or not isinstance(sidecar, Mapping)
        or re.fullmatch(
            r"settlement_clock\.\d{8}\.jsonl", str(sidecar.get("name") or "")
        )
        is None
        or re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("raw_archive_manifests_sha256") or "")
        )
        is None
    ):
        raise ValueError("invalid settlement-lag CLOB custody attestation")
    _validate_portable_identity(sidecar, expected_name=None)
    _validate_portable_identity(
        value.get("archive_snapshot"), expected_name="archive-manifests.json"
    )
    _validate_portable_identity(
        value.get("strict_input_binding"), expected_name="input-binding.json"
    )


def _load_clock_attestation(path: str | Path) -> Mapping[str, Any]:
    try:
        attestation = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid settlement-lag clock attestation") from exc
    if not isinstance(attestation, Mapping):
        raise TypeError("invalid settlement-lag clock attestation")
    return attestation


def _validate_clock_attestation(
    path: str | Path,
    expected_inputs: Mapping[str, Any],
    frozen: Mapping[str, Any],
) -> None:
    attestation = _load_clock_attestation(path)
    if not isinstance(attestation, Mapping) or (
        attestation.get("schema") != CLOCK_ATTESTATION_SCHEMA
        or attestation.get("collector_region") != frozen["market"]["collector_region"]
        or attestation.get("clock_domain") != INPUT_CLOCK_DOMAIN
        or attestation.get("ntp_synchronized") is not True
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(attestation.get("machine_id_sha256") or "")
        )
        or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
            str(attestation.get("boot_id") or ""),
        )
        or attestation.get("inputs") != expected_inputs
    ):
        raise ValueError("invalid settlement-lag clock attestation")
    _validate_clob_custody(attestation.get("clob_custody"))


def _validate_formal_clock_metadata(
    mappings: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Mapping[str, Any]],
    clock_attestation_path: str | Path,
    *,
    maximum_offset_drift_ms: float = 5.0,
) -> None:
    attestation = _load_clock_attestation(clock_attestation_path)
    offsets: list[int] = []
    for mapping in mappings:
        market_id = str(mapping["market_id"])
        row = metadata.get(market_id)
        if row is None or (
            row.get("machine_id_sha256") != attestation.get("machine_id_sha256")
            or row.get("boot_id") != attestation.get("boot_id")
            or row.get("ntp_synchronized") is not True
        ):
            raise ValueError(f"formal clock provenance drift: {market_id}")
        try:
            offsets.append(int(row["realtime_minus_monotonic_ns"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"formal clock provenance drift: {market_id}") from exc
    if offsets and max(offsets) - min(offsets) > maximum_offset_drift_ms * 1_000_000.0:
        raise ValueError(
            "formal collector clock offset drift exceeded the frozen bound"
        )


def build_standard_input_manifest(
    twap_paths: Sequence[str | Path],
    artifact_path: str | Path,
    metadata_paths: Sequence[str | Path],
    clock_attestation_path: str | Path,
) -> dict[str, Any]:
    attestation = _load_clock_attestation(clock_attestation_path)
    _validate_clob_custody(attestation.get("clob_custody"))
    inputs = {
        "twap": [
            _portable_file_identity(path)
            for path in sorted(Path(value) for value in twap_paths)
        ],
        "strict_artifact": _standard_artifact_identity(artifact_path),
        "execution_metadata": [
            _portable_file_identity(path)
            for path in sorted(Path(value) for value in metadata_paths)
        ],
    }
    return {
        "schema": STANDARD_INPUT_MANIFEST_SCHEMA,
        "collector_region": "eu-west-1",
        "clock_domain": INPUT_CLOCK_DOMAIN,
        "strategy_freeze_sha256": EXPECTED_FREEZE_SHA256,
        **inputs,
        "clob_custody": attestation["clob_custody"],
        "clock_attestation": _portable_file_identity(clock_attestation_path),
    }


def validate_standard_input_manifest(
    manifest: Mapping[str, Any],
    twap_paths: Sequence[str | Path],
    artifact_path: str | Path,
    metadata_paths: Sequence[str | Path],
    clock_attestation_path: str | Path,
    frozen: Mapping[str, Any],
) -> None:
    if manifest.get("schema") != STANDARD_INPUT_MANIFEST_SCHEMA:
        raise ValueError("unknown settlement-lag standard input manifest")
    if manifest.get("collector_region") != frozen["market"]["collector_region"]:
        raise ValueError("settlement-lag collector region drift")
    if manifest.get("clock_domain") != INPUT_CLOCK_DOMAIN:
        raise ValueError("settlement-lag input clock domain is not bound to one host")
    if manifest.get("strategy_freeze_sha256") != EXPECTED_FREEZE_SHA256:
        raise ValueError("settlement-lag input freeze identity drift")
    expected = build_standard_input_manifest(
        twap_paths,
        artifact_path,
        metadata_paths,
        clock_attestation_path,
    )
    for field_name in (
        "twap",
        "strict_artifact",
        "execution_metadata",
        "clob_custody",
        "clock_attestation",
    ):
        if manifest.get(field_name) != expected[field_name]:
            raise ValueError(f"settlement-lag {field_name} input identity drift")
    _validate_clock_attestation(
        clock_attestation_path,
        {
            "twap": expected["twap"],
            "strict_artifact": expected["strict_artifact"],
            "execution_metadata": expected["execution_metadata"],
        },
        frozen,
    )


def _iso_seconds(value: object) -> float:
    if not isinstance(value, str):
        raise TypeError("invalid frozen time boundary")
    return datetime.fromisoformat(value).timestamp()


def select_mappings_for_mode(
    mappings: Iterable[Mapping[str, Any]],
    mode: str,
    frozen: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in mappings]
    if mode == "development":
        cutoff = _iso_seconds(frozen["development_data_cutoff"])
        return [row for row in rows if float(row["slot"]) + 300.0 <= cutoff]
    if mode == "formal":
        start = _iso_seconds(frozen["formal_holdout_start"])
        return [row for row in rows if float(row["slot"]) >= start]
    raise ValueError(f"unknown settlement-lag mode: {mode}")


def load_execution_metadata(
    paths: Sequence[str | Path],
    *,
    require_pre_market: bool = False,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    economic_fields = (
        "slug",
        "start_ts",
        "market_id",
        "up_token_id",
        "down_token_id",
        "tick_size",
        "min_size",
        "fee_rate",
        "fee_exponent",
        "taker_only_fee",
        "fees_enabled",
    )
    for path in sorted(Path(value) for value in paths):
        with path.open("rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"{path.name}:{line_number}: invalid execution metadata"
                    ) from exc
                if (
                    not isinstance(row, Mapping)
                    or row.get("schema") != EXECUTION_METADATA_SCHEMA
                ):
                    raise ValueError(
                        f"{path.name}:{line_number}: unknown execution metadata"
                    )
                market_id = str(row.get("market_id") or "")
                if not market_id:
                    raise ValueError(
                        f"{path.name}:{line_number}: missing market identity"
                    )
                normalized = dict(row)
                prior = output.get(market_id)
                if prior is not None and any(
                    prior.get(name) != normalized.get(name) for name in economic_fields
                ):
                    raise ValueError(f"conflicting execution metadata for {market_id}")
                if prior is None or int(normalized["recorded_ns"]) < int(
                    prior["recorded_ns"]
                ):
                    output[market_id] = normalized
    if require_pre_market:
        for market_id, row in output.items():
            if int(row["recorded_ns"]) > int(row["start_ts"]) * 1_000_000_000:
                raise ValueError(
                    f"execution metadata for {market_id} was recorded after market start"
                )
    return output


def load_staged_clob(
    phase_path: str | Path,
    clob_path: str | Path,
    *,
    expected_fee_rate: float = 0.07,
    require_fee_metadata: bool = False,
    require_integrity: bool = False,
    execution_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate a staged eu-strict CLOB stream and recover its direct-token map."""
    phase_path, clob_path = Path(phase_path), Path(clob_path)
    phase = json.loads(phase_path.read_text(encoding="utf-8"))
    if phase.get("schema") != "eu-strict-clob-phase-v1":
        raise ValueError("unknown staged CLOB phase")
    if require_integrity:
        integrity = phase.get("clob_integrity")
        if not isinstance(integrity, Mapping) or not (
            integrity.get("explicit_order") is True
            and integrity.get("physical_sequence_regressions") == 0
            and integrity.get("dropped_outside_epoch") == 0
            and int(integrity.get("max_active_connections", 0)) >= 4
            and integrity.get("closed_at_segment_end") is True
        ):
            raise ValueError("staged CLOB integrity checks failed")
    identity = phase.get("output") or {}
    if (
        not clob_path.is_file()
        or clob_path.stat().st_size != int(identity.get("bytes", -1))
        or _sha256(clob_path) != identity.get("sha256")
    ):
        raise ValueError("staged CLOB identity drift")
    raw_markets = phase.get("markets")
    if not isinstance(raw_markets, Mapping) or not raw_markets:
        raise ValueError("staged CLOB has no market registry")
    mappings = []
    markets: dict[str, dict[str, Any]] = {}
    for market_id, raw in raw_markets.items():
        market = dict(raw)
        if str(market.get("market_id")) != str(market_id):
            raise ValueError("staged market identity mismatch")
        recorded = dict((execution_metadata or {}).get(str(market_id)) or {})
        for name in (
            "tick_size",
            "min_size",
            "fee_rate",
            "fee_exponent",
            "taker_only_fee",
            "fees_enabled",
        ):
            if name in recorded:
                market[name] = recorded[name]
        for name in ("slug", "start_ts", "up_token_id", "down_token_id"):
            if name in recorded and str(recorded[name]) != str(market.get(name)):
                raise ValueError(
                    f"staged market {name} differs from recorded execution metadata"
                )
        raw_fee = market.get("fee_rate")
        if raw_fee in (None, ""):
            if require_fee_metadata:
                raise ValueError("staged market is missing frozen fee metadata")
            fee_rate = float(expected_fee_rate)
        else:
            fee_rate = float(raw_fee)
        if not math.isclose(fee_rate, float(expected_fee_rate), abs_tol=1e-12):
            raise ValueError(
                f"staged market fee-rate drift: {fee_rate} != {expected_fee_rate}"
            )
        if require_fee_metadata:
            required = {
                "tick_size": 0.01,
                "min_size": 5.0,
                "fee_exponent": 1.0,
                "taker_only_fee": True,
                "fees_enabled": True,
            }
            for name, expected in required.items():
                actual = market.get(name)
                if actual in (None, ""):
                    raise ValueError(f"staged market is missing frozen {name} metadata")
                if isinstance(expected, bool):
                    valid = actual is expected
                else:
                    valid = math.isclose(float(actual), expected, abs_tol=1e-12)
                if not valid:
                    raise ValueError(f"staged market {name} drift")
        mapping = {
            "market_id": str(market_id),
            "slot": int(market["start_ts"]),
            "up_token_id": str(market["up_token_id"]),
            "down_token_id": str(market["down_token_id"]),
            "fee_rate": fee_rate,
        }
        mappings.append(mapping)
        markets[str(market_id)] = market
    mappings.sort(key=lambda row: (row["slot"], row["market_id"]))
    return mappings, markets


def _quantiles(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def at(probability: float) -> float:
        index = (len(ordered) - 1) * probability
        lower, upper = math.floor(index), math.ceil(index)
        if lower == upper:
            return ordered[lower]
        weight = index - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "min": ordered[0],
        "p10": at(0.10),
        "p50": at(0.50),
        "p90": at(0.90),
        "max": ordered[-1],
    }


def _paired_control(
    primary_rows: Sequence[Mapping[str, Any]],
    control_rows: Sequence[Mapping[str, Any]],
    horizon_ms: float,
) -> dict[str, Any]:
    PairKey = tuple[str, str, str, int, float]

    def at_horizon(
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[PairKey, Mapping[str, Any]], bool]:
        output: dict[PairKey, Mapping[str, Any]] = {}
        complete = True
        for row in rows:
            if not math.isclose(
                float(row["evaluation_ms"]),
                float(horizon_ms),
                abs_tol=1e-9,
            ):
                continue
            market_id = str(row["market_id"])
            run_id = str(row.get("observation_run_id") or "")
            artifact = str(row.get("artifact_files_sha256") or "")
            try:
                slot = int(row["protocol_slot"])
            except (KeyError, TypeError, ValueError):
                slot = 0
            if not run_id or re.fullmatch(r"[0-9a-f]{64}", artifact) is None or not slot:
                complete = False
            key = (artifact, run_id, market_id, slot, float(horizon_ms))
            if key in output:
                raise ValueError(f"duplicate control observation for {key}")
            output[key] = row
        return output, complete

    primary, primary_identity_complete = at_horizon(primary_rows)
    control, control_identity_complete = at_horizon(control_rows)
    primary_keys = set(primary)
    control_keys = set(control)
    pair_keys = sorted(primary_keys & control_keys)
    missing = sorted(primary_keys - control_keys)
    extra = sorted(control_keys - primary_keys)
    revision_tainted = sorted(
        key
        for key in pair_keys
        if bool(control[key].get("source_revision_after_decision"))
    )
    unresolved = [
        key
        for key in pair_keys
        if (
            float(primary[key].get("filled_shares") or 0.0) > 0
            and primary[key].get("pnl") is None
        )
        or (
            float(control[key].get("filled_shares") or 0.0) > 0
            and control[key].get("pnl") is None
        )
    ]
    identity_complete = primary_identity_complete and control_identity_complete
    structurally_complete = bool(
        identity_complete and not missing and not extra and not revision_tainted
    )
    if unresolved or not structurally_complete:
        return {
            "primary_opportunities": len(primary),
            "control_opportunities": len(control),
            "paired_opportunities": len(pair_keys),
            "pairing_coverage": len(pair_keys) / len(primary) if primary else 0.0,
            "missing_controls": len(missing),
            "extra_controls": len(extra),
            "revision_tainted_controls": len(revision_tainted),
            "identity_complete": identity_complete,
            "resolved": False,
            "primary_pnl_per_opportunity": None,
            "control_pnl_per_opportunity": None,
            "incremental_pnl_per_opportunity": None,
        }
    count = len(pair_keys)
    primary_pnl = sum(float(primary[key].get("pnl") or 0.0) for key in pair_keys)
    control_pnl = sum(float(control[key].get("pnl") or 0.0) for key in pair_keys)
    return {
        "primary_opportunities": len(primary),
        "control_opportunities": len(control),
        "paired_opportunities": count,
        "pairing_coverage": count / len(primary) if primary else 0.0,
        "missing_controls": 0,
        "extra_controls": 0,
        "revision_tainted_controls": 0,
        "identity_complete": identity_complete,
        "resolved": True,
        "primary_pnl_per_opportunity": primary_pnl / count if count else None,
        "control_pnl_per_opportunity": control_pnl / count if count else None,
        "incremental_pnl_per_opportunity": (primary_pnl - control_pnl) / count
        if count
        else None,
    }


def evaluate(
    signals: Mapping[str, Sequence[SettlementSignal]],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    signal_audit: Mapping[str, int],
    config: StrategyConfig | None = None,
) -> dict[str, Any]:
    config = config or StrategyConfig()
    summary = summarize_execution(rows, signals, config=config)
    primary_key = str(int(config.primary_evaluation_ms))
    final = summary[FINAL_VARIANT][primary_key]
    prefinal_pair = _paired_control(
        rows.get(FINAL_VARIANT, ()),
        rows.get(PREFINAL_VARIANT, ()),
        config.primary_evaluation_ms,
    )
    wrong_pair = _paired_control(
        rows.get(FINAL_VARIANT, ()),
        rows.get(WRONG_SIDE_VARIANT, ()),
        config.primary_evaluation_ms,
    )
    exact_source = bool(
        signal_audit.get("official_mismatches", 0) == 0
        and signal_audit.get("unresolved_official", 0) == 0
        and signal_audit.get("post_decision_revisions", 0) == 0
        and signal_audit.get("official_matches", 0) == len(signals[FINAL_VARIANT])
        and all(
            not signal.source_revision_after_decision
            for variant in (PREFINAL_VARIANT, WRONG_SIDE_VARIANT)
            for signal in signals.get(variant, ())
        )
    )
    statistical = bool(
        final["fills"] >= config.minimum_fills
        and final["opportunity_days"] >= config.minimum_days
        and final["day_cluster_lower_99"] is not None
        and final["day_cluster_lower_99"] > 0
        and final["multiplicity_adjusted_p"] < 0.01
    )
    controls = bool(
        prefinal_pair["resolved"]
        and prefinal_pair["paired_opportunities"]
        == prefinal_pair["primary_opportunities"]
        and prefinal_pair["pairing_coverage"] == 1.0
        and prefinal_pair["incremental_pnl_per_opportunity"] is not None
        and prefinal_pair["incremental_pnl_per_opportunity"] > 0
        and wrong_pair["resolved"]
        and wrong_pair["paired_opportunities"] == wrong_pair["primary_opportunities"]
        and wrong_pair["pairing_coverage"] == 1.0
        and wrong_pair["control_pnl_per_opportunity"] is not None
        and wrong_pair["control_pnl_per_opportunity"] <= 0
    )
    paper_gate = bool(
        exact_source
        and statistical
        and controls
        and final["net_cents_per_share"] is not None
        and final["net_cents_per_share"] > 0
    )
    return {
        "strategy_fingerprint": EXPECTED_FREEZE_SHA256,
        "execution": summary,
        "signal_audit": dict(signal_audit),
        "proof_lead_ms": _quantiles(
            [signal.proof_lead_ms for signal in signals[FINAL_VARIANT]]
        ),
        "paired_controls": {
            "prefinality": prefinal_pair,
            "wrong_side": wrong_pair,
        },
        "gate": {
            "passes": False,
            "paper_gate_passes": paper_gate,
            "exact_source_matches_official": exact_source,
            "statistical_gate": statistical,
            "controls_gate": controls,
            "requires_bounded_live_fak_capacity_calibration": True,
            "paper_depth_warning": (
                "displayed effective depth does not prove priority against hidden competing FAKs"
            ),
        },
    }


def run_staged(
    twap_paths: Sequence[str | Path],
    clob_path: str | Path,
    phase_path: str | Path,
    metadata_paths: Sequence[str | Path],
    official_outcomes: Mapping[str, str],
    config: StrategyConfig | None = None,
    *,
    mode: str,
    input_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if mode == "formal":
        if config is not None:
            raise ValueError(
                "formal settlement-lag replay cannot override the frozen config"
            )
        raise ValueError(
            "formal settlement-lag replay requires a standard immutable artifact"
        )
    if mode != "development":
        raise ValueError(f"unknown settlement-lag mode: {mode}")
    if input_manifest is not None:
        raise ValueError(
            "staged development replay cannot claim a formal input manifest"
        )
    frozen = load_freeze()
    config = config or config_from_freeze(frozen)
    execution_metadata = (
        load_execution_metadata(metadata_paths) if metadata_paths else {}
    )
    mappings, _ = load_staged_clob(
        phase_path,
        clob_path,
        expected_fee_rate=config.fee_rate,
        require_fee_metadata=mode == "formal",
        execution_metadata=execution_metadata,
    )
    mappings = select_mappings_for_mode(mappings, mode, frozen)
    tape = (
        collect_twap_points_exact(iter_raw_lines_exact(twap_paths))
        if mode == "formal"
        else collect_twap_points(iter_raw_lines(twap_paths))
    )
    signals, audit = generate_signals(mappings, tape, official_outcomes, config)
    events = h_replay_archive.iter_normalized_events(clob_path, family="clob")
    rows = replay_execution(signals, events, mappings, config)
    result = evaluate(signals, rows, audit, config)
    result["twap_tape"] = {
        "points": len(tape.points),
        "ambiguous_payloads": len(tape.ambiguous_payload_ms),
        "audit": tape.audit,
    }
    result["sample_mode"] = mode
    result["formal_input_bound"] = input_manifest is not None
    return result


def _validate_formal_execution_metadata(
    mappings: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Mapping[str, Any]],
    config: StrategyConfig,
) -> None:
    required = {
        "tick_size": config.tick,
        "min_size": config.base_shares,
        "fee_rate": config.fee_rate,
        "fee_exponent": 1.0,
        "taker_only_fee": True,
        "fees_enabled": True,
    }
    for mapping in mappings:
        market_id = str(mapping["market_id"])
        row = metadata.get(market_id)
        if row is None:
            raise ValueError(
                f"formal market lacks pre-market execution metadata: {market_id}"
            )
        identity = {
            "market_id": market_id,
            "start_ts": int(mapping["slot"]),
            "up_token_id": str(mapping["up_token_id"]),
            "down_token_id": str(mapping["down_token_id"]),
        }
        if any(str(row.get(name)) != str(value) for name, value in identity.items()):
            raise ValueError(f"formal execution metadata identity drift: {market_id}")
        for name, expected in required.items():
            actual = row.get(name)
            if isinstance(expected, bool):
                valid = actual is expected
            else:
                try:
                    if actual is None:
                        raise TypeError
                    valid = math.isclose(float(actual), float(expected), abs_tol=1e-12)
                except (TypeError, ValueError):
                    valid = False
            if not valid:
                raise ValueError(f"formal execution metadata {name} drift: {market_id}")


def run_standard_artifact(
    artifact_path: str | Path,
    twap_paths: Sequence[str | Path],
    metadata_paths: Sequence[str | Path],
    clock_attestation_path: str | Path,
    input_manifest: Mapping[str, Any] | None,
    config: StrategyConfig | None = None,
    *,
    mode: str,
) -> dict[str, Any]:
    """Replay one immutable standard artifact without accepting caller-supplied outcomes."""
    if mode == "formal" and config is not None:
        raise ValueError(
            "formal settlement-lag replay cannot override the frozen config"
        )
    if mode not in {"development", "formal"}:
        raise ValueError(f"unknown settlement-lag mode: {mode}")
    frozen = load_freeze()
    strategy = config or config_from_freeze(frozen)
    if mode == "formal" and input_manifest is None:
        raise ValueError("formal settlement-lag replay requires a bound input manifest")
    if input_manifest is not None:
        validate_standard_input_manifest(
            input_manifest,
            twap_paths,
            artifact_path,
            metadata_paths,
            clock_attestation_path,
            frozen,
        )

    root = _standard_artifact_root(artifact_path)
    before = {
        name: (
            (root / name).stat().st_size,
            (root / name).stat().st_mtime_ns,
            (root / name).stat().st_ino,
        )
        for name in STANDARD_ARTIFACT_FILES
    }
    identity = _standard_artifact_identity(root)
    if (
        identity.get("collector_region") != frozen["market"]["collector_region"]
        or identity.get("receipt_race_ready") is not True
    ):
        raise ValueError("standard artifact is not eligible eu-west receipt data")

    mappings = list(
        h_replay_archive.iter_market_mappings(
            root / "market_registry.csv.gz",
        )
    )
    mapping_ids = [str(row["market_id"]) for row in mappings]
    if len(set(mapping_ids)) != len(mapping_ids):
        raise ValueError("duplicate market mapping")
    mappings = select_mappings_for_mode(mappings, mode, frozen)
    metadata = load_execution_metadata(
        metadata_paths,
        require_pre_market=mode == "formal",
    )
    if mode == "formal":
        _validate_formal_execution_metadata(mappings, metadata, strategy)
        _validate_formal_clock_metadata(
            mappings,
            metadata,
            clock_attestation_path,
            maximum_offset_drift_ms=float(
                frozen["execution"].get("maximum_clock_offset_drift_ms", 5.0)
            ),
        )

    outcomes: dict[str, str] = {}
    result_available_ms: dict[str, float] = {}
    mapping_by_market = {str(row["market_id"]): row for row in mappings}
    for row in h_replay_archive.iter_outcomes(root / "market_outcomes.csv.gz"):
        market_id = str(row["market_id"])
        if market_id in outcomes:
            raise ValueError(f"duplicate market outcome: {market_id}")
        outcomes[market_id] = str(row["winner"])
        mapping = mapping_by_market.get(market_id)
        if mapping is not None:
            recorded_ms = float(row["recv_ms"])
            if not math.isfinite(recorded_ms):
                raise ValueError(
                    f"market outcome has invalid receipt time: {market_id}"
                )
            result_available_ms[market_id] = max(
                recorded_ms,
                (float(mapping["slot"]) + 300.0) * 1_000.0,
            )

    tape = (
        collect_twap_points_exact(iter_raw_lines_exact(twap_paths))
        if mode == "formal"
        else collect_twap_points(iter_raw_lines(twap_paths))
    )
    signals, audit = generate_signals(mappings, tape, outcomes, strategy)
    events = h_replay_archive.iter_normalized_events(
        root / "clob_events.jsonl.gz",
        family="clob",
    )
    if mode == "formal":
        events = require_exact_clob_receipts(events)
    rows = replay_execution(signals, events, mappings, strategy)
    artifact_files_sha256 = strategy_fingerprint(identity["files"])
    for variant_rows in rows.values():
        for row in variant_rows:
            market_id = str(row["market_id"])
            result_time = result_available_ms.get(market_id)
            row["result_time_ms"] = result_time
            row["observation_available_ms"] = max(
                float(row["evaluation_time_ms"]),
                float(result_time) if result_time is not None else math.inf,
            )
            row["observation_run_id"] = str(identity.get("run_id") or root.name)
            row["artifact_files_sha256"] = artifact_files_sha256
            row["artifact_started_ms"] = identity.get("started_ms")
            row["artifact_ended_ms"] = identity.get("ended_ms")
            row["artifact_collector_region"] = identity.get("collector_region")
    apply_settlement_costs(rows, result_available_ms, strategy)

    after = {
        name: (
            (root / name).stat().st_size,
            (root / name).stat().st_mtime_ns,
            (root / name).stat().st_ino,
        )
        for name in STANDARD_ARTIFACT_FILES
    }
    if after != before:
        raise ValueError("standard artifact changed during settlement-lag replay")

    result = evaluate(signals, rows, audit, strategy)
    result["twap_tape"] = {
        "points": len(tape.points),
        "ambiguous_payloads": len(tape.ambiguous_payload_ms),
        "audit": tape.audit,
    }
    result["sample_mode"] = mode
    result["formal_input_bound"] = input_manifest is not None
    result["artifact"] = identity
    result["rows"] = rows
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--twap", nargs="+", required=True, help="raw rtds_cl gzip files"
    )
    parser.add_argument("--mode", choices=("development", "formal"), required=True)
    parser.add_argument("--input-manifest", help="bound settlement-lag-input-v1 JSON")
    parser.add_argument("--artifact", help="standard immutable strict artifact")
    parser.add_argument("--clock-attestation", help="same-host clock attestation JSON")
    parser.add_argument(
        "--execution-metadata",
        nargs="*",
        default=[],
        help="recorded settlement_meta daily JSONL files",
    )
    parser.add_argument("--clob-events")
    parser.add_argument("--clob-phase")
    parser.add_argument(
        "--outcomes", help="JSON object mapping market_id to Up or Down"
    )
    args = parser.parse_args()
    if args.mode == "formal":
        if not all((args.artifact, args.clock_attestation, args.input_manifest)):
            raise SystemExit(
                "formal settlement-lag replay requires a standard immutable artifact, "
                "clock attestation and input manifest"
            )
        result = run_standard_artifact(
            args.artifact,
            args.twap,
            args.execution_metadata,
            args.clock_attestation,
            json.loads(Path(args.input_manifest).read_text(encoding="utf-8")),
            mode="formal",
        )
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return
    if any((args.clob_events, args.clob_phase, args.outcomes)):
        if not all((args.clob_events, args.clob_phase, args.outcomes)):
            raise SystemExit(
                "--clob-events, --clob-phase and --outcomes must be supplied together"
            )
        raw_outcomes = json.loads(Path(args.outcomes).read_text(encoding="utf-8"))
        if not isinstance(raw_outcomes, Mapping) or any(
            winner not in {"Up", "Down"} for winner in raw_outcomes.values()
        ):
            raise SystemExit("invalid official outcomes JSON")
        result = run_staged(
            args.twap,
            args.clob_events,
            args.clob_phase,
            args.execution_metadata,
            {str(key): str(value) for key, value in raw_outcomes.items()},
            mode=args.mode,
            input_manifest=(
                json.loads(Path(args.input_manifest).read_text(encoding="utf-8"))
                if args.input_manifest
                else None
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return
    tape = collect_twap_points(iter_raw_lines(args.twap))
    result = {
        "points": len(tape.points),
        "ambiguous_payloads": len(tape.ambiguous_payload_ms),
        "audit": tape.audit,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
