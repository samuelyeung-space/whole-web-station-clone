"""Recompute paper-ledger economics without changing the upstream ledger.

The upstream report calls the equal-weighted mean of each trade's PnL/share
"per-share PnL".  That statistic is not the realized PnL per filled share when
trade sizes differ.  This audit preserves both values and makes the weighting
difference explicit.  It performs no strategy replay and submits no orders.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


SCHEMA = "paper-ledger-weighting-audit-v1"
REQUIRED = {"slug", "tau", "shares", "cost", "won", "pnl"}


def _number(value: str, label: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f"invalid {label}")
    return result


def _boolean(value: str) -> bool:
    if value not in {"True", "False"}:
        raise ValueError("won must be True or False")
    return value == "True"


def audit(path: str | Path) -> dict:
    path = Path(path)
    raw = path.read_bytes()
    groups: dict[int, list[dict]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError("ledger is missing required columns")
        for row_number, row in enumerate(reader, start=2):
            try:
                tau = int(row["tau"])
                if tau <= 0:
                    raise ValueError("invalid tau")
                slug = row["slug"]
                if not slug or not isinstance(slug, str):
                    raise ValueError("invalid slug")
                identity = (slug, tau)
                if identity in seen:
                    raise ValueError("duplicate slug/tau")
                seen.add(identity)
                groups[tau].append(
                    {
                        "slug": slug,
                        "shares": _number(row["shares"], "shares", positive=True),
                        "cost": _number(row["cost"], "cost", nonnegative=True),
                        "pnl": _number(row["pnl"], "pnl"),
                        "won": _boolean(row["won"]),
                    }
                )
            except ValueError as exc:
                raise ValueError(f"row {row_number}: {exc}") from exc
    if not groups:
        raise ValueError("ledger has no rows")

    result = []
    for tau, rows in sorted(groups.items()):
        shares = math.fsum(row["shares"] for row in rows)
        cost = math.fsum(row["cost"] for row in rows)
        pnl = math.fsum(row["pnl"] for row in rows)
        equal_weighted = math.fsum(row["pnl"] / row["shares"] for row in rows) / len(rows)
        share_weighted = pnl / shares
        result.append(
            {
                "tau_seconds": tau,
                "trades": len(rows),
                "wins": sum(row["won"] for row in rows),
                "markets": len({row["slug"] for row in rows}),
                "filled_shares": shares,
                "cost_usd": cost,
                "net_pnl_usd": pnl,
                "share_weighted_net_pnl_per_share_usd": share_weighted,
                "equal_trade_weighted_mean_pnl_per_share_usd": equal_weighted,
                "weighting_difference_usd_per_share": share_weighted - equal_weighted,
                "roi_on_cost": pnl / cost if cost else None,
            }
        )
    return {
        "schema": SCHEMA,
        "input_file": path.name,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "groups": result,
        "financial_replay_performed": False,
        "formal_oos_result": None,
        "rank7_result": None,
        "note": "Share-weighted PnL is total PnL divided by total filled shares; equal-trade weighting is retained only to reconcile the upstream report.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        result = audit(args.ledger)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "blocked", "reason": str(exc), "rank7_result": None}))
        return 2
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
