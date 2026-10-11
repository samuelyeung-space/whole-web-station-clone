from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import settlement_lag_twap_rec as recorder


def _ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1e9)


def _read(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [
            (int(clock), json.loads(payload))
            for line in stream
            for clock, payload in [line.rstrip("\n").split("\t", 1)]
        ]


def _identity():
    return {
        "recorder_service": "settlement-lag-twap-recorder.service",
        "recorder_unit_path": "/test/twap.service",
        "recorder_unit_sha256": "b" * 64,
        "process_executable_path": "/test/python",
        "process_executable_sha256": "c" * 64,
        "process_cwd": "/test",
        "process_argv": ["/test/python", "/test/settlement_lag_twap_rec.py"],
        "process_script_path": "/test/settlement_lag_twap_rec.py",
        "process_script_sha256": "d" * 64,
        "protocol_freeze_sha256": "e" * 64,
        "protocol_anchor_sha256": "f" * 64,
        "runtime_lock_sha256": "1" * 64,
    }


def test_hour_rotation_closes_old_epoch_and_opens_new_segment(tmp_path):
    boot_id = "12345678-1234-1234-1234-123456789abc"
    samples = iter(
        {
            "machine_id_sha256": "a" * 64,
            "boot_id": boot_id,
            "ntp_synchronized": True,
            "realtime_minus_monotonic_ns": offset,
            "provenance_sampled_ns": 1_800_000_000_000_000_000 + offset,
        }
        for offset in (10, 20, 30, 40)
    )
    sink = recorder.HourlyLifecycleSink(
        "settlement_twap",
        tmp_path,
        provenance_sampler=lambda: next(samples),
        identity_sampler=_identity,
    )
    epoch = _ns("2026-10-13T00:00:01")
    sink.lifecycle(epoch, "connection", epoch, source="rtds_cl")
    sink.write(
        _ns("2026-10-13T00:59:59"),
        '{"topic":"crypto_prices_twap_sixty","payload":{"value":1}}',
    )
    sink.write(
        _ns("2026-10-13T01:00:00.500000"),
        '{"topic":"crypto_prices_twap_sixty","payload":{"value":2}}',
    )
    sink.lifecycle(_ns("2026-10-13T01:00:01"), "disconnect", epoch, source="rtds_cl")
    sink.close()

    hour0 = _read(tmp_path / "settlement_twap.20261013T00.txt.gz")
    hour1 = _read(tmp_path / "settlement_twap.20261013T01.txt.gz")

    assert [row[1].get("_recorder", {}).get("kind") for row in hour0] == [
        "connection",
        None,
        "disconnect",
    ]
    assert hour0[-1][1]["_recorder"]["segment_boundary"] is True
    assert [row[1].get("_recorder", {}).get("kind") for row in hour1] == [
        "connection",
        None,
        "disconnect",
    ]
    assert hour1[0][1]["_recorder"]["segment_boundary"] is True
    assert hour0[0][1]["_recorder"]["realtime_minus_monotonic_ns"] == 10
    assert hour0[-1][1]["_recorder"]["realtime_minus_monotonic_ns"] == 20
    assert hour1[0][1]["_recorder"]["realtime_minus_monotonic_ns"] == 30
    assert hour1[-1][1]["_recorder"]["realtime_minus_monotonic_ns"] == 40
    assert hour1[0][1]["_recorder"]["machine_id_sha256"] == "a" * 64
    assert hour1[0][1]["_recorder"]["boot_id"] == boot_id
    assert hour1[0][1]["_recorder"]["protocol_freeze_sha256"] == "e" * 64
    assert [clock for clock, _ in hour0] == sorted(clock for clock, _ in hour0)
    assert [clock for clock, _ in hour1] == sorted(clock for clock, _ in hour1)


def test_close_is_idempotent_and_closes_active_segment(tmp_path):
    provenance = {
        "machine_id_sha256": "a" * 64,
        "boot_id": "12345678-1234-1234-1234-123456789abc",
        "ntp_synchronized": True,
        "realtime_minus_monotonic_ns": 10,
        "provenance_sampled_ns": 1_800_000_000_000_000_000,
    }
    sink = recorder.HourlyLifecycleSink(
        "settlement_twap",
        tmp_path,
        provenance_sampler=lambda: provenance,
        identity_sampler=_identity,
    )
    epoch = _ns("2026-10-13T02:00:01")
    sink.lifecycle(epoch, "connection", epoch, source="rtds_cl")
    sink.write(
        _ns("2026-10-13T02:00:02"),
        '{"topic":"crypto_prices_twap_sixty","payload":{"value":1}}',
    )

    sink.close(now_ns=_ns("2026-10-13T02:00:03"))
    sink.close(now_ns=_ns("2026-10-13T02:00:04"))

    rows = _read(tmp_path / "settlement_twap.20261013T02.txt.gz")
    assert [row[1].get("_recorder", {}).get("kind") for row in rows] == [
        "connection",
        None,
        "disconnect",
    ]
    assert rows[-1][1]["_recorder"]["segment_boundary"] is True
