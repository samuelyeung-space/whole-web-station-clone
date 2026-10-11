"""Ensure Family 37 has one immutable strict artifact for each closed UTC day."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
from pathlib import Path
from typing import Any

import boto3
import settlement_lag_daily
import settlement_lag_eu_strict
import settlement_lag_forward
import source_race_daily
import strict_prebuild


def _run_phase_exact(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Reuse the frozen shared orchestrator with Family 37's CLOB converter."""
    original = strict_prebuild.eu_strict
    strict_prebuild.eu_strict = settlement_lag_eu_strict
    try:
        return strict_prebuild.run_phase(*args, **kwargs)
    finally:
        strict_prebuild.eu_strict = original


def previous_utc_day(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        raise ValueError("Family37 strict builder requires a timezone-aware clock")
    return (current.astimezone(dt.UTC).date() - dt.timedelta(days=1)).strftime(
        "%Y%m%d"
    )


def next_expected_day(state_root: str | Path) -> str:
    _frozen, freeze_sha256 = settlement_lag_forward.load_freeze()
    return settlement_lag_daily.next_expected_day(state_root, freeze_sha256)


def build_day(
    day: str,
    *,
    data_dir: str | Path,
    poly_dir: str | Path,
    root: str | Path,
    s3: Any,
    bucket: str = source_race_daily.BUCKET,
) -> dict[str, Any]:
    if (
        not day.isdigit()
        or len(day) != 8
        or day >= dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    ):
        raise ValueError("Family37 strict builder only accepts a closed UTC day")
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with (destination / ".prebuild.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for phase in ("source", "clob", "finalize"):
            results.append(
                _run_phase_exact(
                    day,
                    phase,
                    data_dir,
                    poly_dir,
                    destination,
                    s3=s3,
                    bucket=bucket,
                )
            )
    artifact = destination / day / "artifact"
    if not artifact.is_dir() or results[-1].get("status") != "complete":
        raise ValueError("Family37 strict artifact did not complete")
    return {
        "status": "complete",
        "day": day,
        "artifact": str(artifact),
        "phases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day")
    parser.add_argument("--data", default="/home/ubuntu/rec/data")
    parser.add_argument("--poly", default="/home/ubuntu/rec/data_poly")
    parser.add_argument("--root", default=str(strict_prebuild.DEFAULT_ROOT))
    parser.add_argument("--state", default=settlement_lag_daily.DEFAULT_STATE_ROOT)
    parser.add_argument("--bucket", default=source_race_daily.BUCKET)
    args = parser.parse_args()
    day = args.day or next_expected_day(args.state)
    if day >= dt.datetime.now(dt.UTC).strftime("%Y%m%d"):
        print(
            json.dumps(
                {"status": "skipped", "day": day, "reason": "UTC_day_not_closed"},
                sort_keys=True,
            )
        )
        return
    result = build_day(
        day,
        data_dir=args.data,
        poly_dir=args.poly,
        root=args.root,
        s3=boto3.client("s3", region_name="eu-west-1"),
        bucket=args.bucket,
    )
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
