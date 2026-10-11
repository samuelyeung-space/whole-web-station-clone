"""Family 37 archive adapter preserving exact CLOB receipt nanoseconds."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import h_replay_archive as base

ArchiveFormatError = base.ArchiveFormatError


def iter_market_mappings(*args: Any, **kwargs: Any):
    return base.iter_market_mappings(*args, **kwargs)


def iter_outcomes(*args: Any, **kwargs: Any):
    return base.iter_outcomes(*args, **kwargs)


def validate_standard_artifact(*args: Any, **kwargs: Any):
    return base.validate_standard_artifact(*args, **kwargs)


def _receipt_rows(path: str | Path) -> Iterator[tuple[int, int]]:
    for line_number, physical in base._iter_json_objects(
        path,
        generated_single_member=True,
    ):
        try:
            receive_ns = physical["recv_ns"]
            if isinstance(receive_ns, bool):
                raise ValueError("recv_ns must be an integer")
            receive_ns = int(receive_ns)
            if receive_ns < 0:
                raise ValueError("recv_ns must be non-negative")
            base_sequence = int(physical["seq"])
            count = (
                len(physical["changes"])
                if physical.get("kind") == "clob_price_change_batch"
                else 1
            )
            if count <= 0:
                raise ValueError("empty receipt batch")
            for offset in range(count):
                yield base_sequence + offset, receive_ns
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveFormatError(
                f"{Path(path).name}:{line_number}: invalid exact receipt: {exc}"
            ) from exc


def iter_normalized_events(
    path: str | Path,
    *,
    family: str,
) -> Iterator[dict[str, Any]]:
    if family != "clob":
        yield from base.iter_normalized_events(path, family=family)
        return
    previous_ns = -1
    rows = base.iter_normalized_events(path, family=family)
    receipts = _receipt_rows(path)
    for raw_row in rows:
        row = cast(dict[str, Any], raw_row)
        try:
            sequence, receive_ns = next(receipts)
        except StopIteration as exc:
            raise ArchiveFormatError(
                f"{Path(path).name}: exact receipt stream length drift"
            ) from exc
        if int(row["seq"]) != sequence:
            raise ArchiveFormatError(
                f"{Path(path).name}: exact receipt sequence drift"
            )
        if receive_ns < previous_ns:
            raise ArchiveFormatError(
                f"{Path(path).name}: exact receipt clock regressed"
            )
        if float(row["recv_ms"]) != receive_ns / 1_000_000.0:
            raise ArchiveFormatError(
                f"{Path(path).name}: recv_ms/recv_ns drift"
            )
        normalized = dict(row)
        normalized["recv_ns"] = receive_ns
        previous_ns = receive_ns
        yield normalized
    if next(receipts, None) is not None:
        raise ArchiveFormatError(
            f"{Path(path).name}: exact receipt stream length drift"
        )
