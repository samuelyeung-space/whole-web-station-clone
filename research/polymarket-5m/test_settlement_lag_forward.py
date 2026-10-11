from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import settlement_lag as replay
import settlement_lag_forward as forward


def _write_hashed_freeze(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    path.with_suffix(".sha256").write_text(digest + "\n", encoding="ascii")
    return digest


def _freeze(minimum_fills=2, minimum_days=2):
    frozen = replay.load_freeze()
    frozen = json.loads(json.dumps(frozen))
    frozen["stopping"]["minimum_primary_full_fills"] = minimum_fills
    frozen["stopping"]["minimum_utc_days"] = minimum_days
    frozen["stopping"]["family_tests"] = 1
    return frozen


def test_load_freeze_binds_exact_dependency_closure(tmp_path):
    payload = replay.load_freeze()
    payload = json.loads(json.dumps(payload))
    payload["dependencies_sha256"] = {
        name: forward._sha256(forward.ROOT / name)
        for name in sorted(forward.REQUIRED_DEPENDENCIES)
    }
    path = tmp_path / "settlement-lag-freeze.json"
    digest = _write_hashed_freeze(path, payload)

    loaded, loaded_digest = forward.load_freeze(path)

    assert loaded == payload
    assert loaded_digest == digest
    payload["dependencies_sha256"]["settlement_lag.py"] = "0" * 64
    _write_hashed_freeze(path, payload)
    with pytest.raises(ValueError, match="dependency drift"):
        forward.load_freeze(path)


def _row(
    market_id,
    day,
    available_ms,
    *,
    variant=replay.FINAL_VARIANT,
    pnl=0.10,
    evaluation_ms=300.0,
):
    filled = pnl is not None
    direction = "Down" if variant == replay.WRONG_SIDE_VARIANT else "Up"
    won = direction == "Up" if filled else None
    slot = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())
    market_close_ms = (slot + 300) * 1_000.0
    decision_ms = market_close_ms - 500.0
    evaluation_time_ms = decision_ms + evaluation_ms
    result_time_ms = float(available_ms)
    lock_seconds = (result_time_ms - evaluation_time_ms) / 1_000.0
    redemption_cost = 0.01 if filled else 0.0
    annual_factor = 0.10 * lock_seconds / (365.25 * 86_400.0)
    if filled:
        payout = 1.0 if won else 0.0
        all_in_cost = (payout * 5.0 - redemption_cost - pnl) / (
            5.0 * (1.0 + annual_factor)
        )
        gross_pnl = (payout - all_in_cost) * 5.0
        capital_lock_cost = all_in_cost * 5.0 * annual_factor
    else:
        all_in_cost = gross_pnl = capital_lock_cost = None
    return {
        "strategy_id": "twap60-final-race-v3",
        "family": 37,
        "variant": variant,
        "market_id": market_id,
        "protocol_slot": slot,
        "day": day,
        "direction": direction,
        "winner": "Up",
        "won": won if filled else None,
        "source_revision_after_decision": False,
        "evaluation_ms": evaluation_ms,
        "evaluation_time_ms": evaluation_time_ms,
        "observation_available_ms": available_ms,
        "sent": True,
        "filled_shares": 5.0 if filled else 0.0,
        "qualified_fill": filled,
        "reason": "filled" if filled else "insufficient_effective_depth",
        "pnl": pnl,
        "pnl_per_share": pnl / 5.0 if filled else None,
        "gross_pnl": gross_pnl,
        "capital_lock_seconds": lock_seconds if filled else 0.0,
        "capital_lock_cost": capital_lock_cost if filled else 0.0,
        "redemption_cost": redemption_cost,
        "all_in_cost": all_in_cost,
        "fill_price": all_in_cost,
        "fill_fee": 0.0,
        "fill_fee_per_share": 0.0 if filled else None,
        "fixed_limit": 0.97,
        "book_receipt_provenance_exact": filled,
        "decision_ask": 0.97,
        "market_close_ms": market_close_ms,
        "result_time_ms": result_time_ms,
        "proof_payload_ms": market_close_ms
        - (3_000.0 if variant == replay.PREFINAL_VARIANT else 2_000.0),
        "proof_push_ms": decision_ms - 1.0,
        "proof_lead_ms": 500.0,
        "proof_opening": 100.0,
        "proof_closing": 101.0,
        "proof_margin": 0.01,
        "artifact_files_sha256": "a" * 64,
        "artifact_started_ms": slot * 1_000.0,
        "artifact_ended_ms": (slot + 86_400) * 1_000.0 - 1.0,
        "artifact_collector_region": "eu-west-1",
        "observation_run_id": f"run-{day}",
        "match_book_clock": "receipt_ms",
        "match_level_clock": "original_local_receipt_ms",
        "decision_execution_path": "native_ask",
        "effective_execution_path": "native_ask",
    }


def _market_rows(market_id, day, available_ms, pnl=0.10):
    return [
        _row(market_id, day, available_ms, pnl=pnl),
        _row(
            market_id,
            day,
            available_ms,
            variant=replay.PREFINAL_VARIANT,
            pnl=0.15,
        ),
        _row(
            market_id,
            day,
            available_ms,
            variant=replay.WRONG_SIDE_VARIANT,
            pnl=-0.10,
        ),
    ]


def test_merge_is_idempotent_and_rejects_same_key_payload_drift():
    row = _row("m", "2026-10-15", 1_792_022_701_000.0, pnl=0.20)

    assert forward.merge_rows([], [row], _freeze()) == [row]
    assert forward.merge_rows([row], [row], _freeze()) == [row]
    with pytest.raises(ValueError, match="observation conflict"):
        forward.merge_rows(
            [row],
            [dict(row, note="payload_drift")],
            _freeze(),
        )


def test_validate_row_rejects_fills_above_frozen_base_shares():
    row = _row("overfill", "2026-10-15", 1_792_022_701_000.0, pnl=0.20)
    row["filled_shares"] = 5.000001

    with pytest.raises(ValueError, match="exceeds frozen base shares"):
        forward.merge_rows([], [row], _freeze())


def test_signal_clock_cannot_drift_across_execution_horizons():
    row = _row("m", "2026-10-15", 1_792_022_701_000.0, pnl=0.20)
    later = dict(row)
    later["evaluation_ms"] = 500.0
    later["proof_lead_ms"] = 700.0
    later["proof_push_ms"] = row["proof_push_ms"] - 200.0

    merged = forward.merge_rows([], [row, later], _freeze())
    with pytest.raises(ValueError, match="signal drift across horizons"):
        forward.evaluate(merged, _freeze())


def test_control_integrity_rejects_a_missing_control():
    frozen = _freeze(minimum_fills=1, minimum_days=1)
    rows = [
        row
        for row in _market_rows(
            "missing-wrong",
            "2026-10-15",
            1_792_022_701_000.0,
            pnl=0.20,
        )
        if row["variant"] != replay.WRONG_SIDE_VARIANT
    ]
    rows.extend(
        _row(
            "missing-wrong",
            "2026-10-15",
            1_792_022_701_000.0,
            variant=variant,
            pnl=pnl,
            evaluation_ms=500.0,
        )
        for variant, pnl in (
            (replay.FINAL_VARIANT, 0.20),
            (replay.PREFINAL_VARIANT, 0.15),
        )
    )

    result = forward.evaluate(forward.merge_rows([], rows, frozen), frozen)

    assert result["control_integrity"]["complete"] is False
    assert result["control_integrity"]["primary_opportunities"] == 2
    assert result["control_integrity"]["missing_wrong_side"] == 2
    assert result["gate"]["controls_gate"] is False
    assert result["gate"]["paper_gate_passes"] is False


def test_control_integrity_rejects_a_revision_tainted_control():
    frozen = _freeze(minimum_fills=1, minimum_days=1)
    rows = _market_rows(
        "revised-prefinal",
        "2026-10-15",
        1_792_022_701_000.0,
        pnl=0.20,
    )
    for row in rows:
        if row["variant"] == replay.PREFINAL_VARIANT:
            row["source_revision_after_decision"] = True

    result = forward.evaluate(forward.merge_rows([], rows, frozen), frozen)

    assert result["control_integrity"]["complete"] is False
    assert result["control_integrity"]["revision_tainted_controls"] == 1
    assert result["gate"]["controls_gate"] is False
    assert result["gate"]["paper_gate_passes"] is False


def test_control_integrity_rejects_a_control_identity_mismatch():
    frozen = _freeze(minimum_fills=1, minimum_days=1)
    rows = _market_rows(
        "wrong-run",
        "2026-10-15",
        1_792_022_701_000.0,
        pnl=0.20,
    )
    for row in rows:
        if row["variant"] == replay.WRONG_SIDE_VARIANT:
            row["observation_run_id"] = "different-run"

    result = forward.evaluate(forward.merge_rows([], rows, frozen), frozen)

    integrity = result["control_integrity"]
    assert integrity["complete"] is False
    assert integrity["missing_wrong_side"] == 1
    assert integrity["unmatched_wrong_side"] == 1
    assert result["gate"]["controls_gate"] is False
    assert result["gate"]["paper_gate_passes"] is False


def test_control_integrity_rejects_duplicate_controls():
    frozen = _freeze(minimum_fills=1, minimum_days=1)
    rows = _market_rows(
        "duplicate-prefinal",
        "2026-10-15",
        1_792_022_701_000.0,
        pnl=0.20,
    )
    duplicate = next(
        dict(row) for row in rows if row["variant"] == replay.PREFINAL_VARIANT
    )

    with pytest.raises(ValueError, match="duplicate settlement-lag"):
        forward.evaluate([*rows, duplicate], frozen)


def test_stopping_admits_every_row_from_the_threshold_utc_day():
    threshold_available_ms = 1_792_109_101_000.0
    later_same_day_ms = datetime(2026, 10, 17, 1, tzinfo=UTC).timestamp() * 1_000.0
    rows = [
        *_market_rows("a", "2026-10-15", 1_792_022_701_000.0, pnl=0.20),
        *_market_rows("b", "2026-10-16", threshold_available_ms, pnl=0.20),
        *_market_rows("c", "2026-10-16", later_same_day_ms, pnl=0.20),
        *[
            _row(
                "c",
                "2026-10-16",
                later_same_day_ms,
                variant=variant,
                pnl=pnl,
                evaluation_ms=500.0,
            )
            for variant, pnl in (
                (replay.FINAL_VARIANT, 0.20),
                (replay.PREFINAL_VARIANT, 0.15),
                (replay.WRONG_SIDE_VARIANT, -0.10),
            )
        ],
    ]

    cutoff_day = forward.first_stopping_day(rows, _freeze())
    cutoff = forward.first_stopping_cutoff(rows, _freeze())
    selected = forward.rows_at_cutoff(rows, cutoff, cutoff_day=cutoff_day)

    assert cutoff_day == "2026-10-16"
    assert cutoff == pytest.approx(
        datetime(2026, 10, 17, tzinfo=UTC).timestamp() * 1_000.0 - 1.0
    )
    assert {row["market_id"] for row in selected} == {"a", "b", "c"}
    assert any(
        row["market_id"] == "c" and row["evaluation_ms"] == 500.0 for row in selected
    )
    assert max(row["observation_available_ms"] for row in selected) > cutoff


def test_accumulator_pins_first_verdict_and_rejects_new_post_stop_rows(tmp_path):
    ledger = tmp_path / "observations.jsonl"
    status = tmp_path / "status.json"
    verdict = tmp_path / "verdict.json"
    rows = [
        *_market_rows("a", "2026-10-15", 1_792_022_701_000.0, pnl=0.20),
        *_market_rows("b", "2026-10-16", 1_792_109_101_000.0, pnl=0.20),
        *_market_rows("c", "2026-10-16", 1_792_130_701_000.0, pnl=0.20),
    ]

    first = forward.accumulate(
        rows,
        _freeze(),
        "f" * 64,
        ledger_path=ledger,
        status_path=status,
        verdict_path=verdict,
    )
    repeated = forward.accumulate(
        rows,
        _freeze(),
        "f" * 64,
        ledger_path=ledger,
        status_path=status,
        verdict_path=verdict,
    )

    assert first == repeated
    assert first["status"] == "rejected"
    assert first["cutoff_day"] == "2026-10-16"
    assert {
        json.loads(line)["market_id"]
        for line in ledger.read_text(encoding="utf-8").splitlines()
    } == {"a", "b", "c"}
    assert verdict.is_file()
    with pytest.raises(ValueError, match="after the first verdict"):
        forward.accumulate(
            _market_rows("later", "2026-10-17", 1_792_195_501_000.0, pnl=0.20),
            _freeze(),
            "f" * 64,
            ledger_path=ledger,
            status_path=status,
            verdict_path=verdict,
        )
