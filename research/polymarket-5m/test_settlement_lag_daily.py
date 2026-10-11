from __future__ import annotations

import datetime as dt
import csv
import gzip
import inspect
import json
import shutil
from pathlib import Path

import eu_strict
import pytest
import settlement_lag as replay
import settlement_lag_daily as daily
from botocore.exceptions import ClientError

FIRST_DAY = "20261015"
SECOND_DAY = "20261016"
THIRD_DAY = "20261017"
FREEZE_SHA256 = "f" * 64
MACHINE_ID = "a" * 64
BOOT_ID = "12345678-1234-1234-1234-123456789abc"


def _recorder_identity(name: str) -> dict:
    suffix = {"clob": "c", "twap": "d", "metadata": "e"}[name]
    service = {
        "clob": "poly-clob-recorder.service",
        "twap": "settlement-lag-twap-recorder.service",
        "metadata": "settlement-lag-meta-recorder.service",
    }[name]
    script = {
        "clob": "poly_clob_rec.py",
        "twap": "settlement_lag_twap_rec.py",
        "metadata": "settlement_lag_meta_rec.py",
    }[name]
    root = "/home/ubuntu/rec" if name == "clob" else "/home/ubuntu/rec/h-race"
    return {
        "recorder_service": service,
        "recorder_unit_path": f"/etc/systemd/system/{service}",
        "recorder_unit_sha256": suffix * 64,
        "process_executable_path": "/usr/bin/python3.12",
        "process_executable_sha256": "9" * 64,
        "process_cwd": root,
        "process_argv": ["/home/ubuntu/rec/venv/bin/python", f"{root}/{script}"],
        "process_script_path": f"{root}/{script}",
        "process_script_sha256": suffix.upper().lower() * 64,
        "protocol_freeze_sha256": FREEZE_SHA256,
        "protocol_anchor_sha256": "2" * 64,
        "runtime_lock_sha256": "1" * 64,
    }


def _previous_day(day: str) -> str:
    value = dt.datetime.strptime(day, "%Y%m%d").date() - dt.timedelta(days=1)
    return value.strftime("%Y%m%d")


def _archive_manifest(day: str, group: str) -> dict:
    if group == "poly":
        names = [f"poly_clob.{day}T{hour:02d}.jsonl.gz" for hour in range(24)]
    else:
        routes = sorted(
            {
                route
                for candidates in eu_strict.SOURCE_ROUTES.values()
                for route in candidates
            }
        )
        names = [
            f"{route}.{day}T{hour:02d}.txt.gz" for route in routes for hour in range(24)
        ]
    return {
        "day": day,
        "group": group,
        "files": [
            {
                "file": name,
                "size": 10,
                "sha256": "b" * 64,
                "gzip_ok": True,
                "uploaded_ok": True,
            }
            for name in names
        ],
    }


def _day_bounds_ms(day: str) -> tuple[float, float]:
    start = dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.UTC)
    start_ms = start.timestamp() * 1_000.0
    return start_ms + 1.0, start_ms + 86_400_000.0 - 1.0


def _write_strict_day(root: Path, day: str) -> None:
    day_root = root / day
    artifact = day_root / "artifact"
    artifact.mkdir(parents=True)
    started_ms, ended_ms = _day_bounds_ms(day)
    manifest = {
        "schema": "polymarket-5m-strict-replay-v2",
        "complete": True,
        "receipt_race_ready": True,
        "run_id": f"eu-west-{day}",
        "collector_region": "eu-west-1",
        "started_ms": started_ms,
        "ended_ms": ended_ms,
    }
    manifest_path = artifact / "manifest.json"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    for name in daily.archive_daily.STRICT_FILES:
        target = artifact / name
        if name == "market_registry.csv.gz":
            day_start = int(
                dt.datetime.strptime(day, "%Y%m%d")
                .replace(tzinfo=dt.UTC)
                .timestamp()
            )
            with gzip.open(target, "wt", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=(
                        "market_id",
                        "start_ts",
                        "up_token_id",
                        "down_token_id",
                        "updated_at",
                        "fee_rate",
                    ),
                )
                writer.writeheader()
                for slot in range(day_start, day_start + 86_400, 300):
                    writer.writerow(
                        {
                            "market_id": f"market-{slot}",
                            "start_ts": slot,
                            "up_token_id": f"up-{slot}",
                            "down_token_id": f"down-{slot}",
                            "updated_at": slot - 300,
                            "fee_rate": 0.07,
                        }
                    )
        elif target != manifest_path:
            target.write_text("{}\n", encoding="utf-8")
    rec = _archive_manifest(day, "rec")
    poly = _archive_manifest(day, "poly")
    (day_root / "archive-manifests.json").write_text(
        json.dumps({"rec": rec, "poly": poly}) + "\n", encoding="utf-8"
    )
    binding = {
        "schema": "eu-strict-input-binding-v1",
        "day": day,
        "raw_archive_manifests_sha256": (
            daily.shared_daily.strict_prebuild.raw_archive_identity(rec, poly)
        ),
        "strict_manifest_sha256": daily.archive_daily._sha256(manifest_path),
    }
    (artifact / daily.archive_daily.ARTIFACT_BINDING).write_text(
        json.dumps(binding) + "\n", encoding="utf-8"
    )


def _metadata_row(start: int, recorded_ns: int) -> dict:
    return {
        "schema": replay.EXECUTION_METADATA_SCHEMA,
        "recorded_ns": recorded_ns,
        "slug": f"btc-updown-5m-{start}",
        "start_ts": start,
        "market_id": f"market-{start}",
        "up_token_id": f"up-{start}",
        "down_token_id": f"down-{start}",
        "tick_size": 0.01,
        "min_size": 5.0,
        "fee_rate": 0.07,
        "fee_exponent": 1.0,
        "taker_only_fee": True,
        "fees_enabled": True,
        "machine_id_sha256": MACHINE_ID,
        "boot_id": BOOT_ID,
        "ntp_synchronized": True,
        "realtime_minus_monotonic_ns": 1_700_000_000_000_000_000,
        "provenance_sampled_ns": recorded_ns,
        **_recorder_identity("metadata"),
    }


def _write_side_inputs(twap_root: Path, metadata_root: Path, day: str) -> None:
    twap_root.mkdir(parents=True, exist_ok=True)
    metadata_root.mkdir(parents=True, exist_ok=True)
    day_start = int(
        dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.UTC).timestamp()
    )
    payloads_by_hour: dict[str, set[int]] = {}
    for slot in range(day_start, day_start + 86_400, 300):
        for payload_ms in (
            slot * 1_000 - 2_000,
            (slot + 300) * 1_000 - 3_000,
            (slot + 300) * 1_000 - 2_000,
        ):
            hour_name = dt.datetime.fromtimestamp(
                payload_ms / 1_000,
                tz=dt.UTC,
            ).strftime("%Y%m%dT%H")
            payloads_by_hour.setdefault(hour_name, set()).add(payload_ms)
    expected_hours = [f"{_previous_day(day)}T23", *[f"{day}T{h:02d}" for h in range(24)]]
    for hour_name in expected_hours:
        path = twap_root / f"settlement_twap.{hour_name}.txt.gz"
        if path.is_file():
            continue
        hour_start = int(
            dt.datetime.strptime(hour_name, "%Y%m%dT%H")
            .replace(tzinfo=dt.UTC)
            .timestamp()
            * 1_000_000_000
        )
        epoch = hour_start + 1
        connection = {
            "_recorder": {
                "schema": "recorder-lifecycle-v1",
                "kind": "connection",
                "epoch": epoch,
                "source": "rtds_cl",
                "machine_id_sha256": MACHINE_ID,
                "boot_id": BOOT_ID,
                "ntp_synchronized": True,
                "realtime_minus_monotonic_ns": 1_700_000_000_000_000_000,
                "provenance_sampled_ns": epoch,
                **_recorder_identity("twap"),
            }
        }
        disconnect = {
            "_recorder": {
                "schema": "recorder-lifecycle-v1",
                "kind": "disconnect",
                "epoch": epoch,
                "source": "rtds_cl",
                "machine_id_sha256": MACHINE_ID,
                "boot_id": BOOT_ID,
                "ntp_synchronized": True,
                "realtime_minus_monotonic_ns": 1_700_000_000_000_000_000,
                "provenance_sampled_ns": hour_start + 3_600_000_000_000 - 1,
                **_recorder_identity("twap"),
            }
        }
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(f"{epoch}\t{json.dumps(connection)}\n")
            for payload_ms in sorted(payloads_by_hour.get(hour_name, ())):
                twap = {
                    "topic": "crypto_prices_twap_sixty",
                    "timestamp": payload_ms,
                    "payload": {
                        "symbol": "btc/usd",
                        "window_s": 60,
                        "timestamp": payload_ms,
                        "value": 100_000.0,
                    },
                }
                receive_ns = payload_ms * 1_000_000 + 1_000_000
                stream.write(f"{receive_ns}\t{json.dumps(twap)}\n")
            stream.write(
                f"{hour_start + 3_600_000_000_000 - 1}\t{json.dumps(disconnect)}\n"
            )
    rows_by_day: dict[str, list[dict]] = {_previous_day(day): [], day: []}
    for start in range(day_start, day_start + 86_400, 300):
        recorded_ns = (start - 300) * 1_000_000_000 + 1
        recorded_day = dt.datetime.fromtimestamp(
            recorded_ns / 1_000_000_000, tz=dt.UTC
        ).strftime("%Y%m%d")
        rows_by_day.setdefault(recorded_day, []).append(
            _metadata_row(start, recorded_ns)
        )
    for metadata_day, rows in rows_by_day.items():
        path = metadata_root / f"settlement_meta.{metadata_day}.jsonl"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        with path.open("a", encoding="utf-8") as stream:
            for row in rows:
                if f'"market_id": "{row["market_id"]}"' not in existing:
                    stream.write(json.dumps(row) + "\n")

    clock_path = metadata_root / f"settlement_clock.{day}.jsonl"
    poly = {row["file"]: row for row in _archive_manifest(day, "poly")["files"]}
    with clock_path.open("w", encoding="utf-8") as stream:
        for hour in range(24):
            target_hour = f"{day}T{hour:02d}"
            archive_row = poly[f"poly_clob.{target_hour}.jsonl.gz"]
            hour_start_ns = int(
                dt.datetime.strptime(target_hour, "%Y%m%dT%H")
                .replace(tzinfo=dt.UTC)
                .timestamp()
                * 1_000_000_000
            )
            active = {
                "schema": daily.CLOB_CLOCK_SCHEMA,
                "evidence_kind": "active_witness",
                "target_hour": target_hour,
                "clob_file": archive_row["file"],
                "service": daily.CLOB_RECORDER_SERVICE,
                "main_pid": 4321,
                "process_starttime_ticks": 12345,
                "clock_ticks_per_second": 100,
                "process_cmdline_sha256": "c" * 64,
                "active_file_dev": 2049,
                "active_file_inode": 10_000 + hour,
                "open_fd": 7,
                "machine_id_sha256": MACHINE_ID,
                "boot_id": BOOT_ID,
                "ntp_synchronized": True,
                "realtime_minus_monotonic_ns": 1_700_000_000_000_000_000,
                "provenance_sampled_ns": hour_start_ns + 1,
                **_recorder_identity("clob"),
            }
            closed = {
                "schema": daily.CLOB_CLOCK_SCHEMA,
                "evidence_kind": "closed_identity",
                "target_hour": target_hour,
                "clob_file": archive_row["file"],
                "bytes": archive_row["size"],
                "sha256": archive_row["sha256"],
                "machine_id_sha256": MACHINE_ID,
                "boot_id": BOOT_ID,
                "ntp_synchronized": True,
                "realtime_minus_monotonic_ns": 1_700_000_000_000_000_000,
                "provenance_sampled_ns": hour_start_ns + 3_600_000_000_000 + 1,
            }
            stream.write(json.dumps(active) + "\n")
            stream.write(json.dumps(closed) + "\n")


class FakeS3:
    def __init__(self, *, fail_upload: bool = False):
        self.fail_upload = fail_upload
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}

    def head_object(self, *, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {
            "ContentLength": len(self.objects[Key]),
            "Metadata": self.metadata[Key],
        }

    def put_object(self, *, Bucket, Key, Body, ContentLength, Metadata, IfNoneMatch):
        del Bucket
        assert IfNoneMatch == "*"
        if self.fail_upload:
            raise RuntimeError("S3 upload failed")
        payload = Body.read()
        assert len(payload) == ContentLength
        self.objects[Key] = payload
        self.metadata[Key] = dict(Metadata)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    strict = tmp_path / "strict-days"
    twap = tmp_path / "twap"
    metadata = tmp_path / "metadata"
    work = tmp_path / "settlement-lag-days"
    state = tmp_path / "settlement-lag-state"
    calls = {"replay": 0, "accumulate": 0, "terminal": False}
    frozen = {
        "schema": "settlement-lag-freeze-v3",
        "strategy_id": "twap60-final-race-v3",
        "economic_family": 37,
        "formal_holdout_start": "2026-10-15T00:00:00Z",
        "execution": {"maximum_clock_offset_drift_ms": 5.0},
        "dependencies_sha256": {
            "settlement-lag-runtime-lock.json": "1" * 64,
            "poly_clob_rec.py": "c" * 64,
            "settlement_lag_twap_rec.py": "d" * 64,
            "settlement_lag_meta_rec.py": "e" * 64,
            "systemd/poly-clob-recorder.service": "c" * 64,
            "systemd/settlement-lag-twap-recorder.service": "d" * 64,
            "systemd/settlement-lag-meta-recorder.service": "e" * 64,
        },
        "recorders": {
            name: {
                "service": identity["recorder_service"],
                "unit_path": identity["recorder_unit_path"],
                "unit_dependency": (
                    "systemd/poly-clob-recorder.service"
                    if name == "clob"
                    else "systemd/settlement-lag-twap-recorder.service"
                    if name == "twap"
                    else "systemd/settlement-lag-meta-recorder.service"
                ),
                "process_executable_path": identity["process_executable_path"],
                "process_executable_sha256": identity["process_executable_sha256"],
                "process_cwd": identity["process_cwd"],
                "process_argv": identity["process_argv"],
                "process_script_path": identity["process_script_path"],
                "script_dependency": {
                    "clob": "poly_clob_rec.py",
                    "twap": "settlement_lag_twap_rec.py",
                    "metadata": "settlement_lag_meta_rec.py",
                }[name],
            }
            for name, identity in (
                (name, _recorder_identity(name))
                for name in ("clob", "twap", "metadata")
            )
        },
        "safety": {
            "paper_only": True,
            "wallet_access": False,
            "production_orders": False,
        },
    }

    monkeypatch.setattr(daily, "_utc_today", lambda: "20261018")
    monkeypatch.setattr(
        daily.runtime,
        "protocol_identity",
        lambda: {
            "protocol_freeze_sha256": FREEZE_SHA256,
            "protocol_anchor_sha256": "2" * 64,
            "runtime_lock_sha256": "1" * 64,
        },
    )
    monkeypatch.setattr(
        daily.shared_daily.archive,
        "validate_standard_artifact",
        lambda path: {
            "manifest": json.loads(
                (Path(path) / "manifest.json").read_text(encoding="utf-8")
            ),
            "counts": {},
        },
    )
    monkeypatch.setattr(
        daily,
        "_load_freeze",
        lambda _path=daily.replay.FREEZE_PATH: (frozen, FREEZE_SHA256),
    )

    def run_standard_artifact(
        artifact_path,
        twap_paths,
        metadata_paths,
        clock_attestation_path,
        input_manifest,
        config=None,
        *,
        mode,
    ):
        del config
        calls["replay"] += 1
        assert mode == "formal"
        assert len(twap_paths) == 25
        assert [Path(path).name for path in twap_paths] == [
            f"settlement_twap.{_previous_day(Path(artifact_path).parent.name)}T23.txt.gz",
            *[
            f"settlement_twap.{Path(artifact_path).parent.name}T{hour:02d}.txt.gz"
            for hour in range(24)
            ],
        ]
        assert len(metadata_paths) == 2
        attestation = json.loads(
            Path(clock_attestation_path).read_text(encoding="utf-8")
        )
        assert attestation["machine_id_sha256"] == MACHINE_ID
        assert attestation["boot_id"] == BOOT_ID
        assert len(input_manifest["twap"]) == 25
        assert len(input_manifest["execution_metadata"]) == 2
        day = Path(artifact_path).parent.name
        row = {"run": day, "day": f"{day[:4]}-{day[4:6]}-{day[6:]}"}
        return {
            "sample_mode": "formal",
            "formal_input_bound": True,
            "artifact": {"run_id": f"eu-west-{day}"},
            "rows": {
                replay.FINAL_VARIANT: [row],
                replay.PREFINAL_VARIANT: [],
                replay.WRONG_SIDE_VARIANT: [],
            },
        }

    monkeypatch.setattr(daily.replay, "run_standard_artifact", run_standard_artifact)

    def accumulate(
        incoming,
        _frozen,
        freeze_sha256,
        *,
        ledger_path,
        status_path,
        verdict_path,
    ):
        calls["accumulate"] += 1
        assert freeze_sha256 == FREEZE_SHA256
        ledger = Path(ledger_path)
        existing = daily.h_forward._read_jsonl(ledger) if ledger.is_file() else []
        combined = [*existing, *incoming]
        daily.source_confirmation._atomic_write_rows(ledger, combined)
        result = {
            "status": "rejected" if calls["terminal"] else "collecting",
            "freeze_sha256": freeze_sha256,
            "rows": len(combined),
        }
        daily.archive_daily._atomic_json(Path(status_path), result)
        if calls["terminal"]:
            daily.archive_daily._atomic_json(Path(verdict_path), result)
        return result

    monkeypatch.setattr(daily.forward, "accumulate", accumulate)
    return {
        "strict": strict,
        "twap": twap,
        "metadata": metadata,
        "work": work,
        "state": state,
        "calls": calls,
        "frozen": frozen,
    }


def _prepare(harness, day: str) -> None:
    _write_strict_day(harness["strict"], day)
    _write_side_inputs(harness["twap"], harness["metadata"], day)


def _process(harness, day: str, s3: FakeS3):
    return daily.process_day(
        day,
        harness["strict"],
        harness["twap"],
        harness["metadata"],
        harness["work"],
        harness["state"],
        s3=s3,
        bucket="test-bucket",
    )


def _state_hashes(state: Path) -> dict[str, str | None]:
    return {
        name: daily.archive_daily._sha256(state / name)
        if (state / name).is_file()
        else None
        for name in daily.STATE_FILES
    }


def _rewrite_twap(
    path: Path,
    transform,
) -> None:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = stream.readlines()
    rewritten = transform(rows)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.writelines(rewritten)


def test_first_and_consecutive_closed_days_are_admitted(harness):
    _prepare(harness, FIRST_DAY)
    _prepare(harness, SECOND_DAY)
    s3 = FakeS3()

    first = _process(harness, FIRST_DAY, s3)
    second = _process(harness, SECOND_DAY, s3)

    assert first["status"] == second["status"] == "complete"
    admission = json.loads(
        (harness["state"] / daily.ADMISSION).read_text(encoding="utf-8")
    )
    assert admission["day"] == SECOND_DAY
    rows = daily.h_forward._read_jsonl(harness["state"] / daily.OBSERVATIONS)
    assert [row["run"] for row in rows] == [FIRST_DAY, SECOND_DAY]
    assert harness["calls"] == {
        "replay": 2,
        "accumulate": 2,
        "terminal": False,
    }


def test_repeating_same_day_is_idempotent(harness):
    _prepare(harness, FIRST_DAY)
    s3 = FakeS3()
    first = _process(harness, FIRST_DAY, s3)
    hashes = _state_hashes(harness["state"])
    object_count = len(s3.objects)

    second = _process(harness, FIRST_DAY, s3)

    assert second == first
    assert _state_hashes(harness["state"]) == hashes
    assert len(s3.objects) == object_count
    assert harness["calls"]["replay"] == 1
    assert harness["calls"]["accumulate"] == 1


def test_non_contiguous_day_is_rejected_before_reading_inputs(harness):
    _prepare(harness, FIRST_DAY)
    _process(harness, FIRST_DAY, FakeS3())

    with pytest.raises(ValueError, match=f"expected {SECOND_DAY}"):
        _process(harness, THIRD_DAY, FakeS3())

    assert not (harness["work"] / THIRD_DAY).exists()


def test_terminal_verdict_prevents_new_day_replay(harness):
    _prepare(harness, FIRST_DAY)
    _prepare(harness, SECOND_DAY)
    harness["calls"]["terminal"] = True
    _process(harness, FIRST_DAY, FakeS3())

    result = _process(harness, SECOND_DAY, FakeS3())

    assert result["status"] == "terminal"
    assert harness["calls"]["replay"] == 1
    assert harness["calls"]["accumulate"] == 1
    assert not (harness["work"] / SECOND_DAY).exists()


def test_persisted_private_input_drift_is_rejected(harness):
    _prepare(harness, FIRST_DAY)
    s3 = FakeS3()
    _process(harness, FIRST_DAY, s3)
    private_twap = (
        harness["work"]
        / FIRST_DAY
        / daily.PRIVATE_SUPPLEMENTAL
        / "twap"
        / f"settlement_twap.{FIRST_DAY}T00.txt.gz"
    )
    private_twap.write_bytes(private_twap.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="input.*drift|manifest.*drift|gzip integrity"):
        _process(harness, FIRST_DAY, s3)


def test_twap_clock_provenance_drift_is_rejected(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["twap"] / f"settlement_twap.{FIRST_DAY}T00.txt.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = stream.read().splitlines()
    clock, payload = rows[0].split("\t", 1)
    marker = json.loads(payload)
    marker["_recorder"]["machine_id_sha256"] = "c" * 64
    rows[0] = f"{clock}\t{json.dumps(marker)}"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("\n".join(rows) + "\n")

    with pytest.raises(ValueError, match="TWAP clock provenance drift"):
        _process(harness, FIRST_DAY, FakeS3())


def test_missing_one_required_final_twap_key_is_rejected(harness):
    _prepare(harness, FIRST_DAY)
    day_start = int(
        dt.datetime.strptime(FIRST_DAY, "%Y%m%d")
        .replace(tzinfo=dt.UTC)
        .timestamp()
    )
    missing_payload_ms = (day_start + 300) * 1_000 - 2_000
    path = harness["twap"] / f"settlement_twap.{FIRST_DAY}T00.txt.gz"

    def remove_final(rows):
        output = []
        for line in rows:
            _clock, _separator, raw = line.rstrip("\n").partition("\t")
            payload = json.loads(raw)
            data = payload.get("payload") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("timestamp") == missing_payload_ms:
                continue
            output.append(line)
        return output

    _rewrite_twap(path, remove_final)
    with pytest.raises(ValueError, match="TWAP payload coverage is incomplete"):
        _process(harness, FIRST_DAY, FakeS3())


def test_one_usable_twap_frame_per_hour_is_not_complete_coverage(harness):
    _prepare(harness, FIRST_DAY)
    for path in sorted(harness["twap"].glob("settlement_twap.*.txt.gz")):
        def keep_first(rows):
            output = []
            kept = False
            for line in rows:
                _clock, _separator, raw = line.rstrip("\n").partition("\t")
                payload = json.loads(raw)
                if payload.get("topic") == "crypto_prices_twap_sixty":
                    if kept:
                        continue
                    kept = True
                output.append(line)
            return output

        _rewrite_twap(path, keep_first)
    with pytest.raises(ValueError, match="TWAP payload coverage is incomplete"):
        _process(harness, FIRST_DAY, FakeS3())


def test_wrong_symbol_does_not_count_as_a_usable_twap_frame(harness):
    _prepare(harness, FIRST_DAY)
    path = (
        harness["twap"]
        / f"settlement_twap.{_previous_day(FIRST_DAY)}T23.txt.gz"
    )

    def change_symbol(rows):
        output = []
        for line in rows:
            clock, _separator, raw = line.rstrip("\n").partition("\t")
            payload = json.loads(raw)
            if payload.get("topic") == "crypto_prices_twap_sixty":
                payload["payload"]["symbol"] = "eth/usd"
                line = f"{clock}\t{json.dumps(payload)}\n"
            output.append(line)
        return output

    _rewrite_twap(path, change_symbol)
    with pytest.raises(ValueError, match="closed usable epoch"):
        _process(harness, FIRST_DAY, FakeS3())


def test_missing_market_clock_sample_is_rejected(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_meta.{FIRST_DAY}.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="market clock coverage"):
        _process(harness, FIRST_DAY, FakeS3())


def test_restarted_metadata_recorder_can_resample_same_market(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_meta.{FIRST_DAY}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["recorded_ns"] += 1
    row["provenance_sampled_ns"] += 1
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")

    result = _process(harness, FIRST_DAY, FakeS3())

    assert result["status"] == "complete"


def test_restarted_metadata_recorder_cannot_cross_boots(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_meta.{FIRST_DAY}.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["recorded_ns"] += 1
    row["provenance_sampled_ns"] += 1
    row["boot_id"] = "22222222-2222-2222-2222-222222222222"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")

    with pytest.raises(ValueError, match="one collector clock"):
        _process(harness, FIRST_DAY, FakeS3())


def test_clob_clock_sidecar_must_match_strict_archive_manifest(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_clock.{FIRST_DAY}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    closed = next(row for row in rows if row["evidence_kind"] == "closed_identity")
    closed["sha256"] = "c" * 64
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="CLOB clock sidecar.*archive"):
        _process(harness, FIRST_DAY, FakeS3())


def test_clob_witness_must_match_frozen_recorder_code_identity(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_clock.{FIRST_DAY}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    active = next(row for row in rows if row["evidence_kind"] == "active_witness")
    active["process_script_sha256"] = "0" * 64
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="recorder process identity drift"):
        _process(harness, FIRST_DAY, FakeS3())


@pytest.mark.parametrize("evidence_kind", ["active_witness", "closed_identity"])
def test_all_clock_sources_share_one_frozen_offset_bound(harness, evidence_kind):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_clock.{FIRST_DAY}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    target = next(row for row in rows if row["evidence_kind"] == evidence_kind)
    target["realtime_minus_monotonic_ns"] += 5_000_001
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="offset drift exceeded"):
        _process(harness, FIRST_DAY, FakeS3())


def test_clob_clock_sidecar_requires_active_and_closed_for_every_hour(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_clock.{FIRST_DAY}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows = [
        row
        for row in rows
        if not (
            row["target_hour"] == f"{FIRST_DAY}T07"
            and row["evidence_kind"] == "active_witness"
        )
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="active/closed hour coverage"):
        _process(harness, FIRST_DAY, FakeS3())


def test_active_clob_witness_must_be_sampled_inside_target_hour(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_clock.{FIRST_DAY}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    active = next(row for row in rows if row["evidence_kind"] == "active_witness")
    active["provenance_sampled_ns"] += 3_600_000_000_000
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="active witness sampling"):
        _process(harness, FIRST_DAY, FakeS3())


def test_reused_active_pid_must_keep_one_process_identity(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["metadata"] / f"settlement_clock.{FIRST_DAY}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    active_rows = [row for row in rows if row["evidence_kind"] == "active_witness"]
    active_rows[7]["process_cmdline_sha256"] = "d" * 64
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="active process identity"):
        _process(harness, FIRST_DAY, FakeS3())


def test_clock_attestation_binds_clob_custody_to_strict_archive(harness):
    _prepare(harness, FIRST_DAY)

    _process(harness, FIRST_DAY, FakeS3())

    attestation = json.loads(
        (harness["work"] / FIRST_DAY / daily.CLOCK_ATTESTATION).read_text(
            encoding="utf-8"
        )
    )
    custody = attestation["clob_custody"]
    assert custody["scope"] == "same-host-active-open-fd-and-post-close-identity"
    assert custody["hours"] == 24
    assert custody["active_witnesses"] == 24
    assert custody["closed_identities"] == 24
    assert custody["sidecar"]["name"] == f"settlement_clock.{FIRST_DAY}.jsonl"
    assert custody["raw_archive_manifests_sha256"] == (
        daily.shared_daily.strict_prebuild.raw_archive_identity(
            _archive_manifest(FIRST_DAY, "rec"),
            _archive_manifest(FIRST_DAY, "poly"),
        )
    )


def test_process_day_has_no_custom_freeze_path() -> None:
    assert "freeze_path" not in inspect.signature(daily.process_day).parameters


def test_process_day_requires_frozen_clock_drift_bound(harness) -> None:
    _prepare(harness, FIRST_DAY)
    del harness["frozen"]["execution"]["maximum_clock_offset_drift_ms"]

    with pytest.raises(ValueError, match="clock offset drift bound"):
        _process(harness, FIRST_DAY, FakeS3())


def test_twap_open_segment_is_rejected(harness):
    _prepare(harness, FIRST_DAY)
    path = harness["twap"] / f"settlement_twap.{FIRST_DAY}T00.txt.gz"
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = stream.read().splitlines()
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("\n".join(rows[:-1]) + "\n")

    with pytest.raises(ValueError, match="not a closed usable epoch"):
        _process(harness, FIRST_DAY, FakeS3())


def test_upload_failure_never_commits_staged_state(harness):
    _prepare(harness, FIRST_DAY)

    with pytest.raises(RuntimeError, match="S3 upload failed"):
        _process(harness, FIRST_DAY, FakeS3(fail_upload=True))

    assert not (harness["state"] / daily.ADMISSION).exists()
    assert all(not (harness["state"] / name).exists() for name in daily.STATE_FILES)
    assert (harness["work"] / FIRST_DAY / "complete.json").is_file()
    assert (harness["work"] / FIRST_DAY / "state-next" / daily.OBSERVATIONS).is_file()


def test_failed_upload_recovers_from_private_snapshot_after_sources_are_gone(harness):
    _prepare(harness, FIRST_DAY)
    with pytest.raises(RuntimeError, match="S3 upload failed"):
        _process(harness, FIRST_DAY, FakeS3(fail_upload=True))
    shutil.rmtree(harness["strict"])
    shutil.rmtree(harness["twap"])
    shutil.rmtree(harness["metadata"])

    result = _process(harness, FIRST_DAY, FakeS3())

    assert result["status"] == "complete"
    assert (harness["state"] / daily.ADMISSION).is_file()


def test_uploaded_day_recovers_from_interrupted_commit(
    harness, monkeypatch: pytest.MonkeyPatch
):
    _prepare(harness, FIRST_DAY)
    s3 = FakeS3()
    original = daily._commit_completed_day

    def interrupted(complete, complete_path, state, day_root):
        del complete_path
        state.mkdir(parents=True, exist_ok=True)
        source = day_root / "state-next" / daily.OBSERVATIONS
        shutil.copyfile(source, state / daily.OBSERVATIONS)
        assert (
            daily.archive_daily._sha256(state / daily.OBSERVATIONS)
            == complete["target_state_sha256"][daily.OBSERVATIONS]
        )
        raise RuntimeError("commit interrupted")

    monkeypatch.setattr(daily, "_commit_completed_day", interrupted)
    with pytest.raises(RuntimeError, match="commit interrupted"):
        _process(harness, FIRST_DAY, s3)
    assert not (harness["state"] / daily.ADMISSION).exists()

    monkeypatch.setattr(daily, "_commit_completed_day", original)
    result = _process(harness, FIRST_DAY, s3)

    assert result["status"] == "complete"
    admission = json.loads(
        (harness["state"] / daily.ADMISSION).read_text(encoding="utf-8")
    )
    assert admission["day"] == FIRST_DAY
    assert _state_hashes(harness["state"]) == admission["state_sha256"]


def test_consecutive_day_recovers_from_interrupted_commit(
    harness, monkeypatch: pytest.MonkeyPatch
):
    _prepare(harness, FIRST_DAY)
    _prepare(harness, SECOND_DAY)
    s3 = FakeS3()
    _process(harness, FIRST_DAY, s3)
    original = daily._commit_completed_day

    def interrupted(complete, complete_path, state, day_root):
        del complete_path
        source = day_root / "state-next" / daily.OBSERVATIONS
        shutil.copyfile(source, state / daily.OBSERVATIONS)
        assert (
            daily.archive_daily._sha256(state / daily.OBSERVATIONS)
            == complete["target_state_sha256"][daily.OBSERVATIONS]
        )
        raise RuntimeError("commit interrupted")

    monkeypatch.setattr(daily, "_commit_completed_day", interrupted)
    with pytest.raises(RuntimeError, match="commit interrupted"):
        _process(harness, SECOND_DAY, s3)

    monkeypatch.setattr(daily, "_commit_completed_day", original)
    result = _process(harness, SECOND_DAY, s3)

    assert result["status"] == "complete"
    admission = json.loads(
        (harness["state"] / daily.ADMISSION).read_text(encoding="utf-8")
    )
    assert admission["day"] == SECOND_DAY
    assert _state_hashes(harness["state"]) == admission["state_sha256"]


def test_open_utc_day_is_rejected(harness, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(daily, "_utc_today", lambda: FIRST_DAY)

    with pytest.raises(ValueError, match="closed UTC day"):
        _process(harness, FIRST_DAY, FakeS3())
