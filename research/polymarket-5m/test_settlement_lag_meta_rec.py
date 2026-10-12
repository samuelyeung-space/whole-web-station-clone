from __future__ import annotations

import calendar
import gzip
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Self

import pytest
import settlement_lag_meta_rec as recorder


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def _market(slug: str) -> list[dict[str, object]]:
    return [
        {
            "markets": [
                {
                    "conditionId": f"condition-{slug}",
                    "clobTokenIds": json.dumps(["up-token", "down-token"]),
                    "outcomes": json.dumps(["Up", "Down"]),
                    "orderPriceMinTickSize": 0.01,
                    "orderMinSize": 5,
                    "feeSchedule": {
                        "rate": 0.07,
                        "exponent": 1,
                        "takerOnly": True,
                    },
                    "feesEnabled": True,
                }
            ]
        }
    ]


def _provenance(boot_id: str, offset: int) -> dict[str, object]:
    return {
        "machine_id_sha256": "a" * 64,
        "boot_id": boot_id,
        "ntp_synchronized": True,
        "realtime_minus_monotonic_ns": offset,
        "provenance_sampled_ns": 1_800_000_000_000_000_000 + offset,
    }


def _identity() -> dict[str, object]:
    return {
        "recorder_service": "test.service",
        "recorder_unit_path": "/test/test.service",
        "recorder_unit_sha256": "b" * 64,
        "process_executable_path": "/test/python",
        "process_executable_sha256": "c" * 64,
        "process_cwd": "/test",
        "process_argv": ["/test/python", "/test/recorder.py"],
        "process_script_path": "/test/recorder.py",
        "process_script_sha256": "d" * 64,
        "protocol_freeze_sha256": "e" * 64,
        "protocol_anchor_sha256": "f" * 64,
        "runtime_lock_sha256": "1" * 64,
    }


def test_each_market_fetch_samples_fresh_host_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = iter(
        [
            _provenance("11111111-1111-1111-1111-111111111111", 10),
            _provenance("22222222-2222-2222-2222-222222222222", 20),
        ]
    )
    monkeypatch.setattr(recorder, "capture_host_provenance", lambda: next(samples))
    monkeypatch.setattr(recorder, "capture_current_recorder_identity", _identity)
    monkeypatch.setattr(
        recorder.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(
            _market(request.full_url.rsplit("=", 1)[-1])
        ),
    )

    first = recorder.fetch_market("btc-updown-5m-1")
    second = recorder.fetch_market("btc-updown-5m-2")

    assert first["boot_id"] == "11111111-1111-1111-1111-111111111111"
    assert second["boot_id"] == "22222222-2222-2222-2222-222222222222"
    assert first["realtime_minus_monotonic_ns"] == 10
    assert second["realtime_minus_monotonic_ns"] == 20
    assert first["protocol_freeze_sha256"] == "e" * 64


def _hour_start_ns(hour: str) -> int:
    return int(
        calendar.timegm(time.strptime(f"{hour}00", "%Y%m%dT%H%M")) * 1_000_000_000
    )


def test_active_clob_witness_proves_service_process_has_target_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clob = tmp_path / "clob"
    evidence = tmp_path / "evidence"
    proc = tmp_path / "proc"
    hour = "20261013T00"
    pid = 4321
    clob.mkdir()
    source = clob / f"poly_clob.{hour}.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write('{"rn":1,"k":"connection"}\n')
    process = proc / str(pid)
    (process / "fd").mkdir(parents=True)
    (process / "stat").write_text(
        f"{pid} (python recorder) S " + " ".join(["0"] * 18 + ["12345"]) + "\n",
        encoding="utf-8",
    )
    (process / "cmdline").write_bytes(b"python\0poly_clob_rec.py\0")
    (process / "fd" / "7").symlink_to(source)
    sampled_ns = _hour_start_ns(hour) + 1_000_000_000
    provenance = _provenance("11111111-1111-1111-1111-111111111111", 10)
    provenance["provenance_sampled_ns"] = sampled_ns
    monkeypatch.setattr(recorder, "capture_host_provenance", lambda: provenance)
    observed_services: list[str] = []

    def main_pid(service_name: str) -> int:
        observed_services.append(service_name)
        return pid

    monkeypatch.setattr(recorder, "_service_main_pid", main_pid)

    path = recorder.record_active_clob_witness(
        clob,
        evidence,
        hour,
        service_name="test-poly.service",
        proc_root=proc,
        recorder_identity_sampler=lambda *_args: _identity(),
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert observed_services == ["test-poly.service", "test-poly.service"]
    assert rows == [
        {
            "schema": recorder.CLOB_CLOCK_SCHEMA,
            "evidence_kind": "active_witness",
            "target_hour": hour,
            "clob_file": source.name,
            "service": "test-poly.service",
            "main_pid": pid,
            "process_starttime_ticks": 12345,
            "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
            "process_cmdline_sha256": hashlib.sha256(
                b"python\0poly_clob_rec.py\0"
            ).hexdigest(),
            "active_file_dev": source.stat().st_dev,
            "active_file_inode": source.stat().st_ino,
            "open_fd": 7,
            **_identity(),
            **provenance,
        }
    ]


def test_closed_clob_hour_is_bound_once_to_same_host_clock_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clob = tmp_path / "clob"
    evidence = tmp_path / "evidence"
    clob.mkdir()
    source = clob / "poly_clob.20261013T00.jsonl.gz"
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write('{"rn":1,"k":"connection"}\n')
    provenance = _provenance("11111111-1111-1111-1111-111111111111", 10)
    provenance["provenance_sampled_ns"] = (
        _hour_start_ns("20261013T00") + 3_600_000_000_000 + 1
    )
    monkeypatch.setattr(recorder, "capture_host_provenance", lambda: provenance)

    path = recorder.record_closed_clob_identity(clob, evidence, "20261013T00")
    repeated = recorder.record_closed_clob_identity(clob, evidence, "20261013T00")

    assert repeated == path
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0] == {
        "schema": recorder.CLOB_CLOCK_SCHEMA,
        "evidence_kind": "closed_identity",
        "target_hour": "20261013T00",
        "clob_file": source.name,
        "bytes": source.stat().st_size,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        **provenance,
    }

    with source.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="CLOB clock sidecar drift"):
        recorder.record_closed_clob_identity(clob, evidence, "20261013T00")


def test_previous_closed_hour_skips_a_missed_identity_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def record(_clob, _output, hour):
        observed.append(hour)
        return Path("evidence.jsonl")

    monkeypatch.setattr(recorder, "record_closed_clob_identity", record)
    current_hour_ns = _hour_start_ns("20261013T01")

    assert recorder.record_previous_closed_clob_hour(
        "clob",
        "evidence",
        now_ns=current_hour_ns + 601_000_000_000,
    ) is None
    assert observed == []
    assert recorder.record_previous_closed_clob_hour(
        "clob",
        "evidence",
        now_ns=current_hour_ns + 300_000_000_000,
    ) == Path("evidence.jsonl")
    assert observed == ["20261013T00"]


def test_external_clob_process_identity_binds_executable_script_cwd_and_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = tmp_path / "proc" / "4321"
    process.mkdir(parents=True)
    cwd = tmp_path / "rec"
    cwd.mkdir()
    executable = tmp_path / "python3.12"
    executable.write_bytes(b"python")
    script = cwd / "poly_clob_rec.py"
    script.write_text("VALUE = 1\n", encoding="utf-8")
    unit = tmp_path / "poly-clob-recorder.service"
    unit.write_text("[Service]\n", encoding="utf-8")
    (process / "exe").symlink_to(executable)
    (process / "cwd").symlink_to(cwd)
    cmdline = b"/venv/bin/python\0poly_clob_rec.py\0/data\0"
    monkeypatch.setattr(recorder, "_service_fragment_path", lambda _name: unit)
    monkeypatch.setattr(
        recorder.runtime,
        "protocol_identity",
        lambda: {
            "protocol_freeze_sha256": "e" * 64,
            "protocol_anchor_sha256": "f" * 64,
            "runtime_lock_sha256": "1" * 64,
        },
    )

    identity = recorder._external_process_identity(
        process,
        cmdline,
        "poly-clob-recorder.service",
    )

    assert identity["process_executable_sha256"] == hashlib.sha256(b"python").hexdigest()
    assert identity["process_script_sha256"] == hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    assert identity["recorder_unit_sha256"] == hashlib.sha256(
        unit.read_bytes()
    ).hexdigest()
    assert identity["process_cwd"] == str(cwd)
    assert identity["process_argv"] == [
        "/venv/bin/python",
        "poly_clob_rec.py",
        "/data",
    ]


def test_active_witness_rejects_fd_closed_during_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hour = "20261013T00"
    clob = tmp_path / "clob"
    clob.mkdir()
    source = clob / f"poly_clob.{hour}.jsonl.gz"
    source.write_bytes(b"open")
    sampled_ns = _hour_start_ns(hour) + 1_000_000_000
    provenance = _provenance("11111111-1111-1111-1111-111111111111", 10)
    provenance["provenance_sampled_ns"] = sampled_ns
    descriptors = iter([[7], []])
    monkeypatch.setattr(recorder, "capture_host_provenance", lambda: provenance)
    monkeypatch.setattr(recorder, "_service_main_pid", lambda _service: 4321)
    monkeypatch.setattr(
        recorder,
        "_process_identity",
        lambda _process: (12345, b"python\0poly_clob_rec.py\0"),
    )
    monkeypatch.setattr(
        recorder,
        "_open_file_descriptors",
        lambda *_args, **_kwargs: next(descriptors),
    )

    with pytest.raises(ValueError, match="changed during active witness"):
        recorder.record_active_clob_witness(
            clob,
            tmp_path / "evidence",
            hour,
            proc_root=tmp_path / "proc",
            recorder_identity_sampler=lambda *_args: _identity(),
        )
