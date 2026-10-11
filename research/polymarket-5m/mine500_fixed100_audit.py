"""Independent cash-event reconciliation and saved-score tape provenance audit.

No new backtest scenarios. Payout at an official outcome's received time is a
cash-reuse assumption, not proof of actual redeem/on-chain spendability.
"""
import argparse
from collections import Counter
from itertools import zip_longest
import json
from pathlib import Path

import numpy as np

import mine500_deep_batch as batch
import mine500_endpoint_model as model
import mine500_fixed100 as fixed
import mine500_replay_v2 as execution


def close(a, b, label):
    if abs(a - b) > 1e-6:
        raise ValueError(label + " reconciliation mismatch")


def distribution(values):
    if not values:
        return dict(n=0, min=None, p50=None, p90=None, max=None)
    return dict(n=len(values), min=min(values), p50=float(np.quantile(values,.5)),
                p90=float(np.quantile(values,.9)), max=max(values))


def cash_events(rows, summary):
    events = []
    for index, row in enumerate(rows):
        events.append((row["decision_ms"], 0, index, "decision", row))
        if row["submitted"]:
            events.append((row["match_ms"], 1, index, "match", row))
        if row["settled"]:
            events.append((row["settlement_ms"], 2, index, "settlement", row))
    cash, reserved, locked, min_available = 1000., 0., 0., 1000.
    peak_locked, peak_reserved, max_open, open_count = 0., 0., 0, 0
    realized, peak_equity, max_dd, cash_rejections = 0., 1000., 0., 0
    event_counts = Counter()
    for when, _, _, kind, row in sorted(events):
        event_counts[kind] += 1
        if kind == "decision":
            close(cash, row["cash_before_decision"], "decision cash")
            if row["submitted"]:
                if cash-reserved+1e-9 < row["reserved_usd_at_decision"]:
                    raise ValueError("submitted without sufficient available cash")
                reserved += row["reserved_usd_at_decision"]
                min_available = min(min_available,cash-reserved)
            if row["status"] == "rejected_insufficient_available_cash":
                cash_rejections += 1
        elif kind == "match":
            reserved -= row["reserved_usd_at_decision"]
            if abs(reserved) < 1e-8:
                reserved = 0.
            if reserved < -1e-7:
                raise ValueError("negative reservation balance")
            cash -= row["cost_usd"]
            if row["cash_after_match"] is not None:
                close(cash, row["cash_after_match"], "match cash")
                min_available = min(min_available,cash-reserved)
            if row["filled_shares"] > 0:
                open_count += 1
                locked += row["cost_usd"]
        else:
            cash += row["payout_usd"]
            locked -= row["cost_usd"]
            open_count -= 1
            realized += row["payout_usd"]-row["cost_usd"]
            peak_equity = max(peak_equity,1000.+realized)
            max_dd = max(max_dd,peak_equity-(1000.+realized))
            close(cash,row["cash_after_settlement"],"settlement cash")
        peak_locked = max(peak_locked,locked)
        peak_reserved = max(peak_reserved,reserved)
        max_open = max(max_open,open_count)
    close(cash,summary["ending_cash_usd"],"ending cash")
    close(min_available,summary["min_available_cash_usd"],"minimum available cash")
    close(reserved,summary["ending_reserved_usd"],"ending reservation")
    close(realized,summary["realized_pnl_usd"],"realized profit")
    close(max_dd,summary["max_realized_drawdown_usd"],"realized drawdown")
    close(locked,0.,"ending open cost")
    return dict(arithmetic_discrepancies=0,event_counts=dict(event_counts),
        ending_cash_usd=cash,minimum_available_cash_usd=min_available,
        peak_pending_order_reservation_usd=peak_reserved,
        peak_unsettled_position_cost_usd=peak_locked,
        max_simultaneously_open_filled_positions=max_open,
        cash_rejections=cash_rejections,ending_open_positions=open_count,
        max_realized_drawdown_usd=max_dd,
        drawdown_basis="realized settlements only; excludes mark-to-market fluctuations and real redemption lag")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root",type=Path,required=True)
    ap.add_argument("--development",type=Path,required=True)
    args = ap.parse_args()
    src,out=args.source_root,args.development
    original = src/"endpoint_models"
    registration=json.loads((original/"registration.json").read_text())
    input_path=src/"endpoint24/combined.jsonl.gz"
    if batch.digest(input_path)!=registration["input_sha256"]:
        raise ValueError("original pre-score input hash mismatch")
    scores={}
    for row in execution.read_jsonl(original/"scores.jsonl.gz"):
        scores[(str(row["market_id"]),row["recv_ms"],row["model_id"])]=row
    rebuilt=model.attach(execution.read_jsonl(input_path),scores)
    actual=execution.read_jsonl(original/"enriched.jsonl.gz")
    markets,outcomes,counts={},{},Counter()
    for expected,row in zip_longest(rebuilt,actual):
        if expected != row:
            raise ValueError("reconstructed scored tape differs from original enriched input")
        counts[row["type"]]+=1
        if row["type"]=="market":markets[row["market_id"]]=row
        if row["type"]=="outcome" and row["market_id"] not in outcomes:outcomes[row["market_id"]]=row
    scenarios=[]
    for name,_ in fixed.SCENARIOS:
        doc=json.loads((out/(name+"_audit.json")).read_text())
        attempts=list(execution.read_jsonl(out/(name+"_attempts.jsonl.gz")))
        for summary in doc["summary"]:
            rows=[r for r in attempts if r["latency_ms"]==summary["latency_ms"]]
            filled=[r for r in rows if r["filled_shares"]>0]
            for row in filled:
                outcome=outcomes[row["market_id"]]
                expected=max(outcome["recv_ms"],outcome["resolved_ms"])
                close(row["settlement_ms"],expected,"outcome cash reuse clock")
            scenarios.append(dict(scenario=name,latency_ms=summary["latency_ms"],
                **cash_events(rows,summary),
                market_end_to_outcome_receive_seconds=distribution([
                    (outcomes[r["market_id"]]["recv_ms"]-markets[r["market_id"]]["end_ms"])/1000
                    for r in filled]),
                market_end_to_cash_reuse_seconds=distribution([
                    (r["settlement_ms"]-markets[r["market_id"]]["end_ms"])/1000 for r in filled]),
                fill_to_cash_reuse_seconds=distribution([(r["settlement_ms"]-r["match_ms"])/1000 for r in filled])))
    report=dict(code_sha256=batch.digest(__file__),
        original_input_sha256=batch.digest(input_path),
        scored_input_sha256=batch.digest(original/"enriched.jsonl.gz"),
        scored_tape_reconstructed_without_refit_and_every_record_equal=True,
        original_record_counts=dict(counts),scenarios=scenarios,
        payout_reuse_assumption="Release payout at max(official outcome local receive time,resolved time), as encoded in original engine; actual redemption, transaction confirmation and spendable cash time were not observed.",
        missing_actual_cash_evidence=["redeem request and confirmation timestamps","spendable collateral balance receipts","redemption fees or gas allocation"],
        actual_cash_reuse_validated=False,new_execution_scenarios=0,formal_oos_result=None)
    batch.write_json(out/"independent_cash_audit.json",report)
    print("input reconstruction and all nine cash ledgers reconciled",flush=True)


if __name__=="__main__":main()
