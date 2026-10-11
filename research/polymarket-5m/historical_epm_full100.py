"""Paired full-100 top-level-or-zero sensitivity of the frozen legacy EPM tape.

This replays all original decision attempts through the shared cash ledger.
It does not filter decisions by outcomes, retrain a model, retry a failed order,
or scale partial fills up to 100. It is not exhaustive L2 FOK or formal OOS.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import historical_epm_chrono_sensitivity as base


POLICY = "full100_top_level_or_zero"


def full_or_zero(attempts):
    result = copy.deepcopy(attempts)
    for row in result:
        if row["requested_shares"] != 100.0:
            raise ValueError("Only the existing 100-share requests are supported")
        quantity = row["potential_filled_shares"]
        if not 0 <= quantity <= 100:
            raise ValueError("Invalid original potential quantity")
        row["original_fak_potential_shares"] = quantity
        row["execution_policy"] = POLICY
        if 0 < quantity < 100:
            row.update(potential_status="rejected_full100_top_depth_insufficient",
                       potential_filled_shares=0.0, fill_price=None, fees_usd=0.0,
                       potential_cost_usd=0.0, potential_payout_usd=0.0)
    return result


def write_rows(path, rows):
    with gzip.open(path, "wt") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    files = [args.source / f"attempts_{ms}ms.jsonl.gz" for ms in base.LATENCIES_MS]
    files += [args.source / x for x in ("first_signals.csv", "model_audit.json", "registration.json")]
    registration = {
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_id": base.CANDIDATE_ID,
        "execution_policy": POLICY,
        "successful_trade_shares": 100,
        "initial_cash_usd": 1000,
        "same_model_signals_prices_and_clocks": True,
        "partial_fills": False,
        "retry": False,
        "new_model_trials": 0,
        "new_execution_scenarios": 3,
        "new_untouched_oos": False,
        "formal_oos_result": None,
        "source_hashes": {p.name: base.digest(p) for p in files},
        "code_hashes": {Path(__file__).name: base.digest(Path(__file__)),
                        Path(base.__file__).name: base.digest(Path(base.__file__))},
        "limitations": [
            "Top-level depth only: deeper liquidity within the limit is unrecorded.",
            "Observed depth is potential execution, not a private fill receipt.",
            "Gamma closedTime is the settlement cash proxy, not proven spendability.",
            "Historical Oct1 partial-day tape; selected later; not untouched OOS.",
        ],
    }
    base.write_json(args.out / "registration.json", registration)
    metrics, comparison = [], []
    for ms in base.LATENCIES_MS:
        with gzip.open(args.source / f"attempts_{ms}ms.jsonl.gz", "rt") as handle:
            original = [json.loads(line) for line in handle]
        old_metrics, _, _ = base.cash_replay(copy.deepcopy(original))
        new_metrics, attempts, ledger = base.cash_replay(full_or_zero(original))
        assert len(attempts) == len(original)
        assert all(r["filled_shares"] in (0.0, 100.0) for r in attempts)
        assert new_metrics["partial_fills"] == 0
        assert new_metrics["filled_shares"] == 100 * new_metrics["filled_orders"]
        new_metrics.update(execution_policy=POLICY,
                           winning_orders=sum(r["filled_shares"] > 0 and r["side"] == r["winner"] for r in attempts),
                           unsuccessful_orders=len(attempts)-new_metrics["filled_orders"])
        metrics.append(new_metrics)
        comparison.append({"latency_ms": ms,
                           "fak_pnl_usd": old_metrics["net_pnl_usd"],
                           "full100_pnl_usd": new_metrics["net_pnl_usd"],
                           "delta_usd": new_metrics["net_pnl_usd"]-old_metrics["net_pnl_usd"],
                           "fak_partial_orders": old_metrics["partial_fills"],
                           "same_decision_market_order": [r["market_id"] for r in original] == [r["market_id"] for r in attempts]})
        write_rows(args.out / f"attempts_{ms}ms.jsonl.gz", attempts)
        write_rows(args.out / f"cash_ledger_{ms}ms.jsonl.gz", ledger)
    base.write_json(args.out / "metrics.json", metrics)
    base.write_json(args.out / "comparison.json", comparison)
    base.write_json(args.out / "completion.json", {
        "completed_execution_scenarios": 3, "same_frozen_signals": True,
        "full100_or_zero_assertions_passed": True, "formal_oos_result": None})
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
