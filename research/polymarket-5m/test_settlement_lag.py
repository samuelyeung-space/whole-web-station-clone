from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime

import pytest
import settlement_lag as sl
import settlement_lag_archive as archive
import settlement_lag_meta_rec as meta_rec


def _lifecycle(kind: str, epoch: int) -> str:
    return json.dumps(
        {
            "_recorder": {
                "schema": "recorder-lifecycle-v1",
                "kind": kind,
                "epoch": epoch,
                "source": "rtds_cl",
            },
        }
    )


def _twap(payload_ms: int, value: float, *, push_ms: int | None = None) -> str:
    return json.dumps(
        {
            "topic": "crypto_prices_twap_sixty",
            "timestamp": payload_ms if push_ms is None else push_ms,
            "payload": {
                "symbol": "btc/usd",
                "timestamp": payload_ms,
                "value": value,
                "window_s": 60,
            },
        }
    )


def _strict_artifact(root):
    root.mkdir()
    payloads = {
        "source_events.jsonl.gz": b"source",
        "clob_events.jsonl.gz": b"clob",
        "market_registry.csv.gz": b"registry",
        "market_outcomes.csv.gz": b"outcomes",
    }
    identities = {}
    for name, content in payloads.items():
        path = root / name
        path.write_bytes(content)
        identities[name] = {"bytes": len(content), "sha256": sl._sha256(path)}
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "polymarket-5m-strict-replay-v2",
                "complete": True,
                "receipt_race_ready": True,
                "collector_region": "eu-west-1",
                "run_id": "eu-west-20261012",
                "started_ms": 1_797_033_600_000.0,
                "ended_ms": 1_797_119_999_999.0,
                "counts": {},
                "converter_validation": {
                    "schema": "eu-strict-converter-validation-v1",
                    "files": identities,
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "input-binding.json").write_text(
        json.dumps(
            {
                "schema": "eu-strict-input-binding-v1",
                "day": "20261012",
                "raw_archive_manifests_sha256": "a" * 64,
                "strict_manifest_sha256": sl._sha256(root / "manifest.json"),
            }
        ),
        encoding="utf-8",
    )
    return root


def _clob_custody() -> dict:
    return {
        "scope": sl.CLOB_CUSTODY_SCOPE,
        "hours": 24,
        "active_witnesses": 24,
        "closed_identities": 24,
        "sidecar": {
            "name": "settlement_clock.20261012.jsonl",
            "bytes": 10,
            "sha256": "b" * 64,
        },
        "archive_snapshot": {
            "name": "archive-manifests.json",
            "bytes": 10,
            "sha256": "c" * 64,
        },
        "strict_input_binding": {
            "name": "input-binding.json",
            "bytes": 10,
            "sha256": "d" * 64,
        },
        "raw_archive_manifests_sha256": "e" * 64,
    }


def test_parser_preserves_source_push_and_receipt_clocks():
    point = sl.parse_twap_frame(
        1_200.250001,
        _twap(1_000, 80_000.25, push_ms=1_100),
        receive_ns=1_200_250_001,
    )

    assert point == sl.TwapPoint(
        payload_ms=1_000.0,
        push_ms=1_100.0,
        receive_ms=1_200.250001,
        value=80_000.25,
        receive_ns=1_200_250_001,
    )
    assert (
        sl.parse_twap_frame(
            1_201.0,
            json.dumps(
                {
                    "topic": "crypto_prices_chainlink",
                    "payload": {
                        "symbol": "btc/usd",
                        "timestamp": 1_000,
                        "value": 80_000,
                    },
                }
            ),
        )
        is None
    )


@pytest.mark.parametrize("delta_ns", [-89, -1, 1, 89])
def test_exact_receipt_nanoseconds_reject_only_post_evaluation_depth(delta_ns):
    slot = 1_797_033_600
    decision_ns = (slot + 300) * 1_000_000_000 - 250_000_000
    decision_ms = decision_ns / 1_000_000.0
    signal = sl.SettlementSignal(
        variant=sl.FINAL_VARIANT,
        market_id="m",
        slot=slot,
        decision_ms=decision_ms,
        direction="Up",
        opening=100.0,
        closing=101.0,
        margin=0.01,
        proof_payload_ms=decision_ms - 1_750.0,
        proof_push_ms=decision_ms - 50.0,
        proof_lead_ms=250.0,
        actual_winner="Up",
        decision_ns=decision_ns,
    )
    mapping = {
        "market_id": "m",
        "slot": slot,
        "up_token_id": "u",
        "down_token_id": "d",
    }

    def event(kind, receive_ns, seq, **extra):
        return {
            "kind": kind,
            "recv_ns": receive_ns,
            "recv_ms": receive_ns / 1_000_000.0,
            "source_ts_ms": None if kind == "clob_connection" else decision_ms,
            "connection_epoch": 1,
            "seq": seq,
            **extra,
        }

    events = [
        event("clob_connection", decision_ns - 2_000_000, 0),
        event(
            "clob_snapshot",
            decision_ns - 1_000_000,
            1,
            market_id="m",
            asset_id="u",
            bids=[],
            asks=[{"price": "0.99", "size": "6"}],
        ),
        event(
            "clob_snapshot",
            decision_ns - 999_999,
            2,
            market_id="m",
            asset_id="d",
            bids=[],
            asks=[],
        ),
        event(
            "clob_price_change",
            decision_ns + 200_000_000 + delta_ns,
            3,
            market_id="m",
            asset_id="u",
            side="SELL",
            price="0.95",
            size="6",
        ),
        event(
            "clob_price_change",
            decision_ns + 250_000_000,
            4,
            market_id="m",
            asset_id="d",
            side="SELL",
            price="0.05",
            size="6",
        ),
    ]

    rows = sl.replay_execution(
        {sl.FINAL_VARIANT: [signal]},
        events,
        [mapping],
        sl.StrategyConfig(),
    )[sl.FINAL_VARIANT]
    row = next(item for item in rows if item["evaluation_ms"] == 200.0)
    if delta_ns > 0:
        assert row["filled_shares"] == 0.0
        assert row["reason"] in {
            "ask_above_limit",
            "exact_receipt_after_evaluation",
        }


@pytest.mark.parametrize("delta_ns", [-89, -1, 1, 89])
def test_exact_fill_gate_uses_integer_nanosecond_boundary(delta_ns):
    signal = sl.SettlementSignal(
        variant=sl.FINAL_VARIANT,
        market_id="m",
        slot=1,
        decision_ms=1_797_033_600_000.0,
        direction="Up",
        opening=100.0,
        closing=101.0,
        margin=0.01,
        proof_payload_ms=0.0,
        proof_push_ms=0.0,
        proof_lead_ms=250.0,
        actual_winner="Up",
        decision_ns=1_797_033_600_000_000_000,
    )
    reason = sl._exact_fill_rejection_reason(
        signal,
        200.0,
        {
            "book_receipt_nanoseconds_exact": True,
            "effective_fill_newest_receive_ns": (
                signal.decision_ns + 200_000_000 + delta_ns
            ),
        },
    )
    assert reason == ("exact_receipt_after_evaluation" if delta_ns > 0 else None)


def test_formal_clob_stream_requires_original_integer_receipt_clock():
    with pytest.raises(ValueError, match="lacks exact recv_ns"):
        list(
            sl.require_exact_clob_receipts(
                [{"kind": "clob_connection", "recv_ms": 1.0, "seq": 0}]
            )
        )


def test_family_archive_adapter_restores_exact_receipts_for_compact_batches(
    tmp_path,
):
    path = tmp_path / "clob_events.jsonl.gz"
    receipts = [1_800_000_000_000_000_001, 1_800_000_000_000_000_089]
    rows = [
        {
            "kind": "clob_connection",
            "recv_ms": receipts[0] / 1_000_000.0,
            "recv_ns": receipts[0],
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 0,
        },
        {
            "kind": "clob_price_change_batch",
            "recv_ms": receipts[1] / 1_000_000.0,
            "recv_ns": receipts[1],
            "source_ts_ms": 1_800_000_000_000.0,
            "connection_epoch": 1,
            "seq": 1,
            "market_id": "m",
            "changes": [
                {
                    "asset_id": "u",
                    "price": "0.40",
                    "size": "5",
                    "side": "SELL",
                },
                {
                    "asset_id": "d",
                    "price": "0.60",
                    "size": "5",
                    "side": "SELL",
                },
            ],
        },
        {
            "kind": "clob_error",
            "recv_ms": receipts[1] / 1_000_000.0,
            "recv_ns": receipts[1],
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 3,
            "error": "segment_boundary",
        },
    ]
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")

    events = list(archive.iter_normalized_events(path, family="clob"))

    assert [event["seq"] for event in events] == [0, 1, 2, 3]
    assert [event["recv_ns"] for event in events] == [
        receipts[0],
        receipts[1],
        receipts[1],
        receipts[1],
    ]


def test_committed_freeze_is_fingerprinted_and_maps_to_runtime_config(tmp_path):
    frozen = sl.load_freeze()
    pristine = json.loads(json.dumps(frozen))

    assert sl.expected_freeze_sha256() == sl.EXPECTED_FREEZE_SHA256
    assert sl.config_from_freeze(frozen) == sl.StrategyConfig()

    frozen["execution"]["fixed_limit"] = 0.96
    tampered = tmp_path / "freeze.json"
    tampered.write_text(json.dumps(frozen), encoding="utf-8")
    tampered.with_suffix(".sha256").write_text(
        sl.EXPECTED_FREEZE_SHA256 + "\n",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        sl.load_freeze(tampered)

    semantic_drift = json.loads(json.dumps(pristine))
    semantic_drift["execution"]["depth_mode"] = "direct_only"
    with pytest.raises(ValueError, match="depth mode"):
        sl.config_from_freeze(semantic_drift)
    semantic_drift = json.loads(json.dumps(pristine))
    semantic_drift["execution"]["after_market_close_policy"] = "allow_fill"
    with pytest.raises(ValueError, match="market-close policy"):
        sl.config_from_freeze(semantic_drift)
    assert (
        sl.parse_twap_frame(
            1_202.0,
            json.dumps(
                {
                    "topic": "crypto_prices_twap_sixty",
                    "timestamp": 1_100,
                    "payload": {
                        "symbol": "eth/usd",
                        "timestamp": 1_000,
                        "value": 2_000,
                        "window_s": 60,
                    },
                }
            ),
        )
        is None
    )


def test_tape_keeps_first_causal_copy_and_dates_conflicting_revisions():
    rows = [
        (900.0, _twap(1_000, 99.0)),  # no proven connection epoch
        (1_000.0, _lifecycle("connection", 7)),
        (1_100.0, _twap(2_000, 100.0)),
        (1_200.0, _twap(2_000, 100.0)),
        (1_300.0, _twap(3_000, 101.0)),
        (1_400.0, _twap(3_000, 102.0)),
        (1_500.0, _lifecycle("disconnect", 7)),
        (1_600.0, _twap(4_000, 103.0)),
    ]

    tape = sl.collect_twap_points(rows)

    assert tape.points == {
        2_000.0: sl.TwapPoint(2_000.0, 2_000.0, 1_100.0, 100.0),
        3_000.0: sl.TwapPoint(3_000.0, 3_000.0, 1_300.0, 101.0),
    }
    assert tape.ambiguous_payload_ms == frozenset({3_000.0})
    assert tape.first_conflict_receive_ms == {3_000.0: 1_400.0}
    assert tape.audit == {
        "connections": 1,
        "disconnects": 1,
        "accepted": 2,
        "same_value_duplicates": 1,
        "conflicting_revisions": 1,
        "outside_connection_epoch": 2,
        "invalid_frames": 0,
    }


def _proof_tape(slot: int = 1_000) -> sl.TwapTape:
    opening = slot * 1_000 - 2_000
    close = (slot + 300) * 1_000 - 2_000
    return sl.TwapTape(
        points={
            float(opening): sl.TwapPoint(
                float(opening), float(opening + 10), float(opening + 20), 100.0
            ),
            float(close - 1_000): sl.TwapPoint(
                float(close - 1_000), float(close - 990), float(close - 900), 100.5
            ),
            float(close): sl.TwapPoint(
                float(close), float(close + 1_700), float(close + 1_750), 101.0
            ),
        },
        ambiguous_payload_ms=frozenset(),
        audit={},
    )


def test_final_signal_is_receipt_causal_and_controls_cannot_change_it():
    slot = 1_000
    mapping = {"market_id": "m", "slot": slot, "up_token_id": "u", "down_token_id": "d"}

    signals, audit = sl.generate_signals(
        [mapping],
        _proof_tape(slot),
        {"m": "Up"},
        sl.StrategyConfig(),
    )

    final = signals[sl.FINAL_VARIANT][0]
    early = signals[sl.PREFINAL_VARIANT][0]
    wrong = signals[sl.WRONG_SIDE_VARIANT][0]
    assert final.direction == "Up"
    assert final.decision_ms == (slot + 300) * 1_000 - 250
    assert final.proof_lead_ms == 250.0
    assert final.actual_winner == "Up"
    assert early.decision_ms < final.decision_ms
    assert early.direction == "Up"
    assert wrong.decision_ms == final.decision_ms
    assert wrong.direction == "Down"
    assert audit["official_matches"] == 1
    assert audit["official_mismatches"] == 0


def test_conflicted_or_missing_proof_never_emits_a_final_signal():
    tape = _proof_tape()
    close_key = 1_298_000.0
    tape = sl.TwapTape(
        points={key: value for key, value in tape.points.items() if key != close_key},
        ambiguous_payload_ms=frozenset({close_key}),
        audit={},
    )

    signals, audit = sl.generate_signals(
        [{"market_id": "m", "slot": 1_000, "up_token_id": "u", "down_token_id": "d"}],
        tape,
        {"m": "Up"},
        sl.StrategyConfig(),
    )

    assert signals[sl.FINAL_VARIANT] == []
    assert signals[sl.WRONG_SIDE_VARIANT] == []
    assert audit["ambiguous_final"] == 1


def test_opening_must_arrive_before_the_final_proof():
    tape = _proof_tape()
    opening_key = 998_000.0
    opening = tape.points[opening_key]
    final = tape.points[1_298_000.0]
    tape = sl.TwapTape(
        points={
            **tape.points,
            opening_key: sl.TwapPoint(
                opening.payload_ms,
                opening.push_ms,
                final.receive_ms + 1.0,
                opening.value,
            ),
        },
        ambiguous_payload_ms=frozenset(),
        audit={},
    )

    signals, audit = sl.generate_signals(
        [{"market_id": "m", "slot": 1_000, "up_token_id": "u", "down_token_id": "d"}],
        tape,
        {"m": "Up"},
        sl.StrategyConfig(),
    )

    assert signals[sl.FINAL_VARIANT] == []
    assert audit["opening_received_after_final_proof"] == 1


def test_late_final_revision_keeps_the_intention_but_fails_source_gate():
    tape = _proof_tape()
    final_key = 1_298_000.0
    tape = sl.TwapTape(
        points=tape.points,
        ambiguous_payload_ms=frozenset({final_key}),
        audit={},
        first_conflict_receive_ms={final_key: 1_299_900.0},
    )
    mapping = {
        "market_id": "m",
        "slot": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }

    signals, audit = sl.generate_signals(
        [mapping],
        tape,
        {"m": "Up"},
        sl.StrategyConfig(),
    )

    assert len(signals[sl.FINAL_VARIANT]) == 1
    assert signals[sl.FINAL_VARIANT][0].source_revision_after_decision is True
    assert audit["post_decision_revisions"] == 1
    rows = sl.replay_execution(signals, _clob_events(), [mapping], sl.StrategyConfig())
    result = sl.evaluate(signals, rows, audit, sl.StrategyConfig())
    assert result["gate"]["exact_source_matches_official"] is False


def _clob_events(slot: int = 1_000):
    proof_receive = (slot + 300) * 1_000 - 250.0
    return [
        {
            "kind": "clob_connection",
            "recv_ms": proof_receive - 500,
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 0,
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 400,
            "source_ts_ms": proof_receive - 410,
            "connection_epoch": 1,
            "seq": 1,
            "market_id": "m",
            "asset_id": "u",
            "bids": [],
            "asks": [{"price": "0.95", "size": "6"}],
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 399,
            "source_ts_ms": proof_receive - 409,
            "connection_epoch": 1,
            "seq": 2,
            "market_id": "m",
            "asset_id": "d",
            "bids": [],
            "asks": [{"price": "0.05", "size": "6"}],
        },
        {
            "kind": "clob_price_change",
            "recv_ms": proof_receive + 250,
            "source_ts_ms": proof_receive + 240,
            "connection_epoch": 1,
            "seq": 3,
            "market_id": "m",
            "asset_id": "u",
            "side": "SELL",
            "price": "0.96",
            "size": "6",
        },
        {
            "kind": "clob_price_change",
            "recv_ms": proof_receive + 501,
            "source_ts_ms": proof_receive + 491,
            "connection_epoch": 1,
            "seq": 4,
            "market_id": "m",
            "asset_id": "d",
            "side": "SELL",
            "price": "0.05",
            "size": "6",
            "best_bid": "0.04",
            "best_ask": "0.05",
        },
    ]


def test_execution_uses_fixed_limit_direct_depth_fee_and_close_cutoff():
    mapping = {
        "market_id": "m",
        "slot": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }
    proof_receive = (mapping["slot"] + 300) * 1_000 - 250.0
    signals, _ = sl.generate_signals(
        [mapping],
        _proof_tape(),
        {"m": "Up"},
        sl.StrategyConfig(),
    )

    rows = sl.replay_execution(signals, _clob_events(), [mapping], sl.StrategyConfig())
    primary = {row["evaluation_ms"]: row for row in rows[sl.FINAL_VARIANT]}

    assert primary[200.0]["fixed_limit"] == 0.97
    assert primary[200.0]["filled_shares"] == pytest.approx(5.0)
    assert primary[200.0]["fill_price"] == pytest.approx(0.95)
    assert primary[200.0]["fill_fee"] > 0
    assert primary[200.0]["pnl"] > 0
    assert primary[200.0]["decision_effective_ask_receive_ms"] == proof_receive - 400
    assert primary[200.0]["effective_fill_oldest_receive_ms"] == proof_receive - 400
    assert primary[200.0]["effective_fill_max_age_ms"] == pytest.approx(600.0)
    assert primary[200.0]["book_receipt_provenance_exact"] is True
    assert primary[200.0]["match_level_clock"] == "original_local_receipt_ms"
    assert primary[300.0]["reason"] == "after_market_close"
    assert primary[300.0]["filled_shares"] == 0
    assert primary[300.0]["filled"] is False
    assert primary[300.0]["eligible_depth"] == 0
    assert primary[300.0]["match_price"] is None
    assert primary[300.0]["cross_close_sensitivity"]["reason"] == "filled"
    assert primary[300.0]["cross_close_sensitivity"]["filled_shares"] == pytest.approx(
        5.0
    )
    assert primary[500.0]["reason"] == "after_market_close"


def test_execution_fee_is_truncated_once_at_venue_precision():
    assert sl._venue_fee(0.123456789) == pytest.approx(0.12345)


def test_execution_deducts_frozen_redemption_and_capital_lock_costs():
    rows = {
        sl.FINAL_VARIANT: [
            {
                "market_id": "m",
                "evaluation_time_ms": 1_000.0,
                "filled_shares": 5.0,
                "all_in_cost": 0.97,
                "won": True,
                "pnl": 0.15,
                "pnl_per_share": 0.03,
            }
        ]
    }
    config = sl.StrategyConfig(capital_annual_rate=0.10, redemption_cost_usd=0.01)

    sl.apply_settlement_costs(rows, {"m": 61_000.0}, config)

    row = rows[sl.FINAL_VARIANT][0]
    assert row["gross_pnl"] == pytest.approx(0.15)
    assert row["capital_lock_seconds"] == 60.0
    assert row["capital_lock_cost"] > 0
    assert row["redemption_cost"] == 0.01
    assert row["pnl"] < 0.14


def test_buy_winner_can_match_the_complementary_loser_bid():
    mapping = {
        "market_id": "m",
        "slot": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }
    signals, _ = sl.generate_signals(
        [mapping],
        _proof_tape(),
        {"m": "Up"},
        sl.StrategyConfig(),
    )
    proof_receive = 1_299_750.0
    events = [
        {
            "kind": "clob_connection",
            "recv_ms": proof_receive - 500,
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 0,
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 400,
            "source_ts_ms": proof_receive - 410,
            "connection_epoch": 1,
            "seq": 1,
            "market_id": "m",
            "asset_id": "u",
            "bids": [],
            "asks": [],
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 399,
            "source_ts_ms": proof_receive - 409,
            "connection_epoch": 1,
            "seq": 2,
            "market_id": "m",
            "asset_id": "d",
            "bids": [{"price": "0.04", "size": "6"}],
            "asks": [{"price": "0.05", "size": "6"}],
        },
        {
            "kind": "clob_price_change",
            "recv_ms": proof_receive + 250,
            "source_ts_ms": proof_receive + 240,
            "connection_epoch": 1,
            "seq": 3,
            "market_id": "m",
            "asset_id": "d",
            "side": "BUY",
            "price": "0.04",
            "size": "6",
            "best_bid": "0.04",
            "best_ask": "0.05",
        },
    ]

    rows = sl.replay_execution(signals, events, [mapping], sl.StrategyConfig())
    primary = {row["evaluation_ms"]: row for row in rows[sl.FINAL_VARIANT]}

    assert primary[200.0]["decision_ask"] == pytest.approx(0.96)
    assert primary[200.0]["fill_price"] == pytest.approx(0.96)
    assert primary[200.0]["filled_shares"] == pytest.approx(5.0)


def test_native_and_complementary_depth_are_never_summed_across_paths():
    mapping = {
        "market_id": "m",
        "slot": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }
    signals, _ = sl.generate_signals(
        [mapping],
        _proof_tape(),
        {"m": "Up"},
        sl.StrategyConfig(),
    )
    proof_receive = 1_299_750.0
    events = [
        {
            "kind": "clob_connection",
            "recv_ms": proof_receive - 500,
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 0,
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 400,
            "source_ts_ms": proof_receive - 410,
            "connection_epoch": 1,
            "seq": 1,
            "market_id": "m",
            "asset_id": "u",
            "bids": [],
            "asks": [{"price": "0.95", "size": "3"}],
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 399,
            "source_ts_ms": proof_receive - 409,
            "connection_epoch": 1,
            "seq": 2,
            "market_id": "m",
            "asset_id": "d",
            "bids": [{"price": "0.04", "size": "3"}],
            "asks": [{"price": "0.05", "size": "3"}],
        },
        {
            "kind": "clob_price_change",
            "recv_ms": proof_receive + 250,
            "source_ts_ms": proof_receive + 240,
            "connection_epoch": 1,
            "seq": 3,
            "market_id": "m",
            "asset_id": "u",
            "side": "SELL",
            "price": "0.95",
            "size": "3",
        },
    ]

    rows = sl.replay_execution(signals, events, [mapping], sl.StrategyConfig())
    primary = {row["evaluation_ms"]: row for row in rows[sl.FINAL_VARIANT]}

    assert primary[200.0]["filled_shares"] == pytest.approx(3.0)
    assert primary[200.0]["reason"] == "partial_fill"
    assert primary[200.0]["qualified_fill"] is False
    assert primary[200.0]["effective_execution_path"] == "complementary_bid"


def test_execution_can_use_a_proven_direct_ask_when_the_other_token_is_not_ready():
    mapping = {
        "market_id": "m",
        "slot": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }
    signals, _ = sl.generate_signals(
        [mapping],
        _proof_tape(),
        {"m": "Up"},
        sl.StrategyConfig(),
    )
    proof_receive = 1_299_750.0
    events = [
        {
            "kind": "clob_connection",
            "recv_ms": proof_receive - 500,
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 0,
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 400,
            "source_ts_ms": proof_receive - 410,
            "connection_epoch": 1,
            "seq": 1,
            "market_id": "m",
            "asset_id": "u",
            "bids": [],
            "asks": [{"price": "0.95", "size": "6"}],
        },
        {
            "kind": "clob_price_change",
            "recv_ms": proof_receive + 250,
            "source_ts_ms": proof_receive + 240,
            "connection_epoch": 1,
            "seq": 2,
            "market_id": "m",
            "asset_id": "u",
            "side": "SELL",
            "price": "0.95",
            "size": "6",
        },
    ]

    rows = sl.replay_execution(signals, events, [mapping], sl.StrategyConfig())
    primary = {row["evaluation_ms"]: row for row in rows[sl.FINAL_VARIANT]}

    assert primary[200.0]["decision_ask"] == pytest.approx(0.95)
    assert primary[200.0]["filled_shares"] == pytest.approx(5.0)


def test_proven_empty_winning_side_is_not_reported_as_an_unavailable_book():
    mapping = {
        "market_id": "m",
        "slot": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }
    signals, _ = sl.generate_signals(
        [mapping],
        _proof_tape(),
        {"m": "Up"},
        sl.StrategyConfig(),
    )
    proof_receive = 1_299_750.0
    events = [
        {
            "kind": "clob_connection",
            "recv_ms": proof_receive - 500,
            "source_ts_ms": None,
            "connection_epoch": 1,
            "seq": 0,
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 400,
            "source_ts_ms": proof_receive - 410,
            "connection_epoch": 1,
            "seq": 1,
            "market_id": "m",
            "asset_id": "u",
            "bids": [],
            "asks": [],
        },
        {
            "kind": "clob_snapshot",
            "recv_ms": proof_receive - 399,
            "source_ts_ms": proof_receive - 409,
            "connection_epoch": 1,
            "seq": 2,
            "market_id": "m",
            "asset_id": "d",
            "bids": [],
            "asks": [{"price": "0.05", "size": "6"}],
        },
        {
            "kind": "clob_price_change",
            "recv_ms": proof_receive + 250,
            "source_ts_ms": proof_receive + 240,
            "connection_epoch": 1,
            "seq": 3,
            "market_id": "m",
            "asset_id": "d",
            "side": "SELL",
            "price": "0.05",
            "size": "6",
        },
    ]

    rows = sl.replay_execution(signals, events, [mapping], sl.StrategyConfig())
    primary = {row["evaluation_ms"]: row for row in rows[sl.FINAL_VARIANT]}

    assert primary[200.0]["sent"] is False
    assert primary[200.0]["decision_reason"] == "no_effective_ask_at_decision"
    assert primary[200.0]["reason"] == "no_effective_ask_at_decision"


def test_formal_and_development_market_windows_never_overlap():
    frozen = sl.load_freeze()
    cutoff = datetime.fromisoformat(frozen["development_data_cutoff"]).timestamp()
    formal = datetime.fromisoformat(frozen["formal_holdout_start"]).timestamp()
    mappings = [
        {"market_id": "dev", "slot": int(cutoff) - 600},
        {"market_id": "gap", "slot": int(cutoff)},
        {"market_id": "formal", "slot": int(formal)},
    ]

    assert [
        row["market_id"]
        for row in sl.select_mappings_for_mode(
            mappings,
            "development",
            frozen,
        )
    ] == ["dev"]
    assert [
        row["market_id"]
        for row in sl.select_mappings_for_mode(
            mappings,
            "formal",
            frozen,
        )
    ] == ["formal"]
    with pytest.raises(ValueError, match="mode"):
        sl.select_mappings_for_mode(mappings, "anything", frozen)


def test_formal_staged_replay_cannot_use_unbound_outcomes_or_custom_config():
    with pytest.raises(ValueError, match="standard immutable artifact"):
        sl.run_staged([], "missing", "missing", [], {}, mode="formal")
    with pytest.raises(ValueError, match="frozen config"):
        sl.run_staged(
            [],
            "missing",
            "missing",
            [],
            {},
            sl.StrategyConfig(theta=0.01),
            mode="formal",
        )


def test_standard_artifact_manifest_binds_outcomes_and_clock_attestation(tmp_path):
    artifact = _strict_artifact(tmp_path / "strict")
    twap = tmp_path / "rtds_cl.20261012T00.txt.gz"
    metadata = tmp_path / "settlement_meta.20261012.jsonl"
    twap.write_bytes(b"twap")
    metadata.write_bytes(b"metadata")
    attestation = tmp_path / "clock-attestation.json"
    attestation.write_text(
        json.dumps(
            sl.clock_attestation_payload(
                [twap],
                artifact,
                [metadata],
                machine_id_sha256="a" * 64,
                boot_id="11111111-1111-1111-1111-111111111111",
                ntp_synchronized=True,
                clob_custody=_clob_custody(),
            )
        ),
        encoding="utf-8",
    )
    manifest = sl.build_standard_input_manifest(
        [twap],
        artifact,
        [metadata],
        attestation,
    )

    sl.validate_standard_input_manifest(
        manifest,
        [twap],
        artifact,
        [metadata],
        attestation,
        sl.load_freeze(),
    )
    assert manifest["clob_custody"] == _clob_custody()
    tampered_attestation = json.loads(attestation.read_text(encoding="utf-8"))
    tampered_attestation["clob_custody"]["active_witnesses"] = 23
    attestation.write_text(json.dumps(tampered_attestation), encoding="utf-8")
    with pytest.raises(ValueError, match="custody"):
        sl.build_standard_input_manifest([twap], artifact, [metadata], attestation)
    attestation.write_text(
        json.dumps(
            sl.clock_attestation_payload(
                [twap],
                artifact,
                [metadata],
                machine_id_sha256="a" * 64,
                boot_id="11111111-1111-1111-1111-111111111111",
                ntp_synchronized=True,
                clob_custody=_clob_custody(),
            )
        ),
        encoding="utf-8",
    )
    (artifact / "market_outcomes.csv.gz").write_bytes(b"relabelled")
    with pytest.raises(ValueError, match="artifact"):
        sl.validate_standard_input_manifest(
            manifest,
            [twap],
            artifact,
            [metadata],
            attestation,
            sl.load_freeze(),
        )


def test_formal_standard_replay_cannot_override_the_frozen_config():
    with pytest.raises(ValueError, match="frozen config"):
        sl.run_standard_artifact(
            "missing",
            [],
            [],
            "missing",
            {},
            config=sl.StrategyConfig(theta=0.01),
            mode="formal",
        )


def test_cli_refuses_formal_tape_only_mode(monkeypatch, tmp_path):
    twap = tmp_path / "twap.gz"
    with gzip.open(twap, "wt", encoding="utf-8") as stream:
        stream.write("")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "settlement_lag.py",
            "--mode",
            "formal",
            "--twap",
            str(twap),
        ],
    )

    with pytest.raises(SystemExit, match="standard immutable artifact"):
        sl.main()


def test_execution_metadata_sidecar_preserves_and_validates_market_fee(tmp_path):
    row = meta_rec.market_metadata(
        "btc-updown-5m-1000",
        {
            "conditionId": "m",
            "clobTokenIds": json.dumps(["up", "down"]),
            "outcomes": json.dumps(["Up", "Down"]),
            "orderPriceMinTickSize": 0.01,
            "orderMinSize": 5,
            "feeSchedule": {"rate": 0.07, "exponent": 1, "takerOnly": True},
            "feesEnabled": True,
        },
        recorded_ns=123,
    )
    path = tmp_path / "settlement_meta.19700101.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    loaded = sl.load_execution_metadata([path])

    assert loaded["m"] == row
    conflicting = dict(row, fee_rate=0.08)
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(conflicting) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="conflicting execution metadata"):
        sl.load_execution_metadata([path])


def test_formal_execution_metadata_must_be_recorded_before_market_start(tmp_path):
    row = meta_rec.market_metadata(
        "btc-updown-5m-1000",
        {
            "conditionId": "m",
            "clobTokenIds": json.dumps(["up", "down"]),
            "outcomes": json.dumps(["Up", "Down"]),
            "orderPriceMinTickSize": 0.01,
            "orderMinSize": 5,
            "feeSchedule": {"rate": 0.07, "exponent": 1, "takerOnly": True},
            "feesEnabled": True,
        },
        recorded_ns=1_001_000_000_000,
    )
    path = tmp_path / "settlement_meta.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="after market start"):
        sl.load_execution_metadata([path], require_pre_market=True)


def test_formal_clock_metadata_must_match_attested_host_and_boot(tmp_path):
    attestation = tmp_path / "clock.json"
    attestation.write_text(
        json.dumps(
            {
                "machine_id_sha256": "a" * 64,
                "boot_id": "11111111-1111-1111-1111-111111111111",
            }
        ),
        encoding="utf-8",
    )
    mapping = {"market_id": "m"}
    row = {
        "machine_id_sha256": "a" * 64,
        "boot_id": "11111111-1111-1111-1111-111111111111",
        "ntp_synchronized": True,
        "realtime_minus_monotonic_ns": 10,
    }

    sl._validate_formal_clock_metadata([mapping], {"m": row}, attestation)
    with pytest.raises(ValueError, match="clock provenance drift"):
        sl._validate_formal_clock_metadata(
            [mapping],
            {"m": dict(row, boot_id="other")},
            attestation,
        )


def test_staged_clob_rejects_missing_or_drifted_formal_fee(tmp_path):
    clob = tmp_path / "clob.jsonl.gz"
    clob.write_bytes(b"events")
    identity = {"bytes": clob.stat().st_size, "sha256": sl._sha256(clob)}
    market = {
        "market_id": "m",
        "start_ts": 1_000,
        "up_token_id": "u",
        "down_token_id": "d",
    }
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps(
            {
                "schema": "eu-strict-clob-phase-v1",
                "output": identity,
                "markets": {"m": market},
                "clob_integrity": {
                    "explicit_order": True,
                    "physical_sequence_regressions": 0,
                    "dropped_outside_epoch": 0,
                    "max_active_connections": 4,
                    "closed_at_segment_end": True,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fee metadata"):
        sl.load_staged_clob(
            phase,
            clob,
            expected_fee_rate=0.07,
            require_fee_metadata=True,
        )
    with pytest.raises(ValueError, match="fee-rate drift"):
        sl.load_staged_clob(
            phase,
            clob,
            expected_fee_rate=0.07,
            require_fee_metadata=True,
            execution_metadata={
                "m": {
                    "tick_size": 0.01,
                    "min_size": 5.0,
                    "fee_rate": 0.072,
                    "fee_exponent": 1.0,
                    "taker_only_fee": True,
                    "fees_enabled": True,
                }
            },
        )


def test_staged_clob_rejects_known_integrity_failure(tmp_path):
    clob = tmp_path / "clob.jsonl.gz"
    clob.write_bytes(b"events")
    identity = {"bytes": clob.stat().st_size, "sha256": sl._sha256(clob)}
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps(
            {
                "schema": "eu-strict-clob-phase-v1",
                "output": identity,
                "markets": {
                    "m": {
                        "market_id": "m",
                        "start_ts": 1_000,
                        "up_token_id": "u",
                        "down_token_id": "d",
                    }
                },
                "clob_integrity": {
                    "explicit_order": True,
                    "physical_sequence_regressions": 1,
                    "dropped_outside_epoch": 0,
                    "max_active_connections": 4,
                    "closed_at_segment_end": True,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="CLOB integrity"):
        sl.load_staged_clob(phase, clob, require_integrity=True)


def test_summary_counts_no_fill_as_zero_intention_to_trade_pnl():
    signal = sl.SettlementSignal(
        variant=sl.FINAL_VARIANT,
        market_id="m",
        slot=1_000,
        decision_ms=1_299_000.0,
        direction="Up",
        opening=100.0,
        closing=101.0,
        margin=1.0,
        proof_payload_ms=1_298_000.0,
        proof_push_ms=1_298_010.0,
        proof_lead_ms=1_000.0,
        actual_winner="Up",
    )
    rows = {
        sl.FINAL_VARIANT: [
            {
                "evaluation_ms": 300.0,
                "sent": True,
                "filled_shares": 5.0,
                "qualified_fill": True,
                "reason": "filled",
                "pnl": 0.10,
                "pnl_per_share": 0.02,
                "won": True,
                "all_in_cost": 0.98,
                "winner": "Up",
                "day": "2026-10-12",
                "market_id": "m",
            },
            {
                "evaluation_ms": 300.0,
                "sent": True,
                "filled_shares": 0.0,
                "qualified_fill": False,
                "reason": "insufficient_effective_depth",
                "pnl": None,
                "pnl_per_share": None,
                "won": None,
                "all_in_cost": None,
                "winner": "Up",
                "day": "2026-10-12",
                "market_id": "n",
            },
        ]
    }

    summary = sl.summarize_execution(
        rows, {sl.FINAL_VARIANT: [signal, signal]}, horizons=(300.0,)
    )

    result = summary[sl.FINAL_VARIANT]["300"]
    assert result["opportunities"] == 2
    assert result["pnl"] == pytest.approx(0.10)
    assert result["pnl_per_opportunity"] == pytest.approx(0.05)
    assert result["fill_rate"] == pytest.approx(0.5)
    assert result["controllable_capacity_lower_bound_shares"] == 0.0


def test_exact_test_uses_only_full_target_fills(monkeypatch):
    signal = sl.SettlementSignal(
        variant=sl.FINAL_VARIANT,
        market_id="m",
        slot=1_000,
        decision_ms=1_299_000.0,
        direction="Up",
        opening=100.0,
        closing=101.0,
        margin=0.01,
        proof_payload_ms=1_298_000.0,
        proof_push_ms=1_298_010.0,
        proof_lead_ms=1_000.0,
        actual_winner="Up",
    )
    seen = []

    def exact(rows):
        seen.append(list(rows))
        return 0.5

    monkeypatch.setattr(sl.h_replay_run, "_exact_pvalue", exact)
    rows = {
        sl.FINAL_VARIANT: [
            {
                "evaluation_ms": 300.0,
                "sent": True,
                "filled_shares": 5.0,
                "qualified_fill": True,
                "reason": "filled",
                "pnl": 0.1,
                "pnl_per_share": 0.02,
                "won": True,
                "all_in_cost": 0.98,
                "winner": "Up",
                "day": "2026-10-12",
                "market_id": "full",
            },
            {
                "evaluation_ms": 300.0,
                "sent": True,
                "filled_shares": 1.0,
                "qualified_fill": False,
                "reason": "partial_fill",
                "pnl": 0.02,
                "pnl_per_share": 0.02,
                "won": True,
                "all_in_cost": 0.98,
                "winner": "Up",
                "day": "2026-10-12",
                "market_id": "partial",
            },
        ]
    }

    summary = sl.summarize_execution(
        rows,
        {sl.FINAL_VARIANT: [signal, signal]},
        horizons=(300.0,),
    )[sl.FINAL_VARIANT]["300"]

    assert len(seen[0]) == 1
    assert seen[0][0]["market_id"] == "full"
    assert summary["exact_test_full_fill_count"] == 1


def test_control_comparison_is_paired_by_market_not_marginal_sample():
    def row(market_id, pnl):
        return {
            "market_id": market_id,
            "protocol_slot": 1_000,
            "evaluation_ms": 300.0,
            "observation_run_id": "run",
            "artifact_files_sha256": "a" * 64,
            "source_revision_after_decision": False,
            "filled_shares": 5.0 if pnl else 0.0,
            "pnl": pnl if pnl else None,
        }

    paired = sl._paired_control(
        [row("shared", 0.0), row("primary-only", 1.0)],
        [row("shared", 0.10)],
        300.0,
    )

    assert paired["primary_opportunities"] == 2
    assert paired["paired_opportunities"] == 1
    assert paired["pairing_coverage"] == pytest.approx(0.5)
    assert paired["missing_controls"] == 1
    assert paired["resolved"] is False


def test_control_comparison_requires_clean_identity_matched_controls():
    def row(variant, *, revised=False):
        return {
            "market_id": "shared",
            "protocol_slot": 1_000,
            "evaluation_ms": 300.0,
            "observation_run_id": "run",
            "artifact_files_sha256": "a" * 64,
            "source_revision_after_decision": revised,
            "filled_shares": 5.0,
            "pnl": 0.20 if variant == sl.FINAL_VARIANT else -0.10,
        }

    clean = sl._paired_control(
        [row(sl.FINAL_VARIANT)],
        [row(sl.PREFINAL_VARIANT)],
        300.0,
    )
    tainted = sl._paired_control(
        [row(sl.FINAL_VARIANT)],
        [row(sl.PREFINAL_VARIANT, revised=True)],
        300.0,
    )

    assert clean["resolved"] is True
    assert clean["pairing_coverage"] == 1.0
    assert clean["incremental_pnl_per_opportunity"] == pytest.approx(0.30)
    assert tainted["resolved"] is False
    assert tainted["revision_tainted_controls"] == 1
