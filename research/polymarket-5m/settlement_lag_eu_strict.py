"""Family 37 strict conversion with exact integer CLOB receipt clocks.

The shared converter is byte-frozen by earlier forward protocols.  Family 37
therefore adds ``recv_ns`` in an isolated adapter instead of mutating that
converter and invalidating prior holdouts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import eu_strict as base

prepare_source = base.prepare_source
finalize = base.finalize


def iter_clob_events(paths: Iterable[Path], segment_end_ms: float):
    markets: dict[str, dict[str, Any]] = {}
    tokens: dict[str, str] = {}
    active: set[tuple[int, int]] = set()
    observed_connections: set[tuple[int, int]] = set()
    physical_sequence: dict[tuple[int, int], int] = {}
    physical_kind: dict[tuple[int, int], str] = {}
    logical_epoch = 0
    sequence = 0
    carryover_deadline_ns = int(
        (
            segment_end_ms
            - base.DAY_MS
            + base.LEADING_CARRYOVER_GRACE_MS
        )
        * 1_000_000
    )
    stats: dict[str, Any] = {
        "explicit_order": True,
        "physical_sequence_regressions": 0,
        "legacy_duplicate_error_annotations": 0,
        "leading_carryover_frames": 0,
        "dropped_outside_epoch": 0,
        "deduplicated_frames": 0,
        "max_active_connections": 0,
        "closed_at_segment_end": False,
        "snapshots": set(),
    }
    last_receive_ns = -1
    for path in sorted(paths):
        for line_number, row in base._iter_json_records(path):
            if not isinstance(row, Mapping) or row.get("rn") is None:
                raise ValueError(
                    f"{path.name}:{line_number}: invalid CLOB envelope"
                )
            receive_ns = int(row["rn"])
            if receive_ns < last_receive_ns:
                raise ValueError(
                    f"{path.name}:{line_number}: CLOB receive clock regressed"
                )
            last_receive_ns = receive_ns
            if row.get("c") == -1 or row.get("k") == "meta":
                base._market_meta(row, markets, tokens)
                continue
            kind = row.get("k")
            if kind in ("connection", "disconnect", "message", "error"):
                try:
                    connection = (int(row["c"]), int(row["e"]))
                    physical = int(row["s"])
                except (KeyError, TypeError, ValueError) as exc:
                    stats["explicit_order"] = False
                    raise ValueError(
                        f"{path.name}:{line_number}: missing CLOB lifecycle"
                    ) from exc
                if kind != "connection" and connection not in observed_connections:
                    key = (
                        "leading_carryover_frames"
                        if receive_ns <= carryover_deadline_ns
                        else "dropped_outside_epoch"
                    )
                    stats[key] += 1
                    continue
                previous = physical_sequence.get(connection, -1)
                if (
                    kind == "error"
                    and physical == previous
                    and physical_kind.get(connection) == "disconnect"
                    and connection not in active
                ):
                    stats["legacy_duplicate_error_annotations"] += 1
                    continue
                if physical != previous + 1:
                    stats["physical_sequence_regressions"] += 1
                physical_sequence[connection] = physical
                physical_kind[connection] = str(kind)
            else:
                stats["explicit_order"] = False
                stats["dropped_outside_epoch"] += 1
                continue
            if kind == "connection":
                observed_connections.add(connection)
                was_empty = not active
                active.add(connection)
                stats["max_active_connections"] = max(
                    stats["max_active_connections"], len(active)
                )
                if was_empty:
                    logical_epoch += 1
                    yield {
                        "kind": "clob_connection",
                        "recv_ms": receive_ns / 1e6,
                        "recv_ns": receive_ns,
                        "source_ts_ms": None,
                        "connection_epoch": logical_epoch,
                        "seq": sequence,
                        "token_count": len(row.get("assets") or ()),
                    }, markets, tokens, stats
                    sequence += 1
                continue
            if kind == "disconnect":
                active.discard(connection)
                if not active:
                    yield {
                        "kind": "clob_error",
                        "recv_ms": receive_ns / 1e6,
                        "recv_ns": receive_ns,
                        "source_ts_ms": None,
                        "connection_epoch": logical_epoch,
                        "seq": sequence,
                        "error": "all_warm_connections_closed",
                    }, markets, tokens, stats
                    sequence += 1
                continue
            if kind == "error":
                continue
            if connection not in active:
                stats["dropped_outside_epoch"] += 1
                continue
            messages = row.get("m")
            for message in messages if isinstance(messages, list) else [messages]:
                if not isinstance(message, Mapping):
                    continue
                event_type = message.get("event_type")
                if event_type == "book":
                    if str(message.get("asset_id", "")) not in tokens:
                        continue
                elif event_type == "price_change":
                    changes = [
                        change
                        for change in message.get("price_changes") or ()
                        if isinstance(change, Mapping)
                        and str(change.get("asset_id", "")) in tokens
                    ]
                    if not changes:
                        continue
                else:
                    continue
                source_ms = base._clock_ms(message.get("timestamp"))
                if source_ms is None:
                    continue
                if event_type == "book":
                    token = str(message.get("asset_id", ""))
                    event = {
                        "kind": "clob_snapshot",
                        "recv_ms": receive_ns / 1e6,
                        "recv_ns": receive_ns,
                        "source_ts_ms": source_ms,
                        "connection_epoch": logical_epoch,
                        "seq": sequence,
                        "connection_id": connection[0],
                        "market_id": str(
                            message.get("market") or tokens[token]
                        ),
                        "asset_id": token,
                        "bids": (
                            message.get("bids")
                            if "bids" in message
                            else message.get("buys") or []
                        ),
                        "asks": (
                            message.get("asks")
                            if "asks" in message
                            else message.get("sells") or []
                        ),
                    }
                    stats["snapshots"].add((logical_epoch, token))
                    yield event, markets, tokens, stats
                    sequence += 1
                    continue
                normalized: list[dict[str, Any]] = []
                for change in changes:
                    item = {
                        "asset_id": str(change["asset_id"]),
                        "price": change.get("price"),
                        "size": change.get("size"),
                        "side": change.get("side"),
                    }
                    for name in ("best_bid", "best_ask"):
                        if name in change:
                            item[name] = change[name]
                    normalized.append(item)
                change_token = str(normalized[0]["asset_id"])
                yield {
                    "kind": "clob_price_change_batch",
                    "recv_ms": receive_ns / 1e6,
                    "recv_ns": receive_ns,
                    "source_ts_ms": source_ms,
                    "connection_epoch": logical_epoch,
                    "seq": sequence,
                    "connection_id": connection[0],
                    "market_id": str(
                        message.get("market") or tokens[change_token]
                    ),
                    "changes": normalized,
                }, markets, tokens, stats
                sequence += len(normalized)
    if active and last_receive_ns >= 0:
        boundary_ns = int(round(segment_end_ms * 1_000_000.0)) - 1_000
        yield {
            "kind": "clob_error",
            "recv_ms": boundary_ns / 1_000_000.0,
            "recv_ns": boundary_ns,
            "source_ts_ms": None,
            "connection_epoch": logical_epoch,
            "seq": sequence,
            "error": "segment_boundary",
        }, markets, tokens, stats
        active.clear()
    stats["closed_at_segment_end"] = not active


def prepare_clob(
    poly_dir: str | Path,
    out: str | Path,
    day: str,
) -> dict[str, Any]:
    """Run the shared writer with the Family 37 exact-clock event reader."""
    original = base.iter_clob_events
    base.iter_clob_events = iter_clob_events
    try:
        return base.prepare_clob(poly_dir, out, day)
    finally:
        base.iter_clob_events = original
