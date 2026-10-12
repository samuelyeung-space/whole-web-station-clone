"""Admit one immutable UTC day into the Family 37 paper-only ledger."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import gzip
import json
import math
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import boto3
import h_forward
import h_replay_archive
import settlement_lag as replay
import settlement_lag_forward as forward
import settlement_lag_runtime as runtime
import source_confirmation
import source_confirmation_daily as shared_daily
import source_race_daily as archive_daily

BUCKET = archive_daily.BUCKET
EVIDENCE_PREFIX = "formal/settlement-lag-v2"
DEFAULT_STRICT_ROOT = "/home/ubuntu/rec/formal/strict-days"
DEFAULT_TWAP_ROOT = "/home/ubuntu/rec/data_settlement_twap"
DEFAULT_METADATA_ROOT = "/home/ubuntu/rec/data_settlement_meta"
DEFAULT_WORK_ROOT = "/home/ubuntu/rec/formal/settlement-lag-days"
DEFAULT_STATE_ROOT = "/home/ubuntu/rec/formal/settlement-lag-state"
FIRST_COMPLETE_DAY = "20261015"
OBSERVATIONS = forward.OBSERVATIONS
STATUS = forward.STATUS
VERDICT = forward.VERDICT
STATE_FILES = (OBSERVATIONS, STATUS, VERDICT)
ADMISSION = "admission.json"
PRIVATE_ARTIFACT = shared_daily.PRIVATE_ARTIFACT
PRIVATE_SUPPLEMENTAL = "settlement-inputs"
CLOCK_ATTESTATION = "clock-attestation.json"
INPUT_MANIFEST = "input-manifest.json"
REPLAY_ROWS = "replay-rows.jsonl"
REPLAY_SUMMARY = "replay-summary.json"
CLOB_CLOCK_SCHEMA = "settlement-lag-clob-clock-sidecar-v3"
CLOB_CLOCK_SCOPE = "same-host-active-open-fd-and-post-close-identity"
CLOB_RECORDER_SERVICE = "poly-clob-recorder.service"
MAXIMUM_CLOB_CUSTODY_LAG_S = 600
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_BOOT_ID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
RECORDER_IDENTITY_FIELDS = frozenset(
    {
        "recorder_service",
        "recorder_unit_path",
        "recorder_unit_sha256",
        "process_executable_path",
        "process_executable_sha256",
        "process_cwd",
        "process_argv",
        "process_script_path",
        "process_script_sha256",
        "protocol_freeze_sha256",
        "protocol_anchor_sha256",
        "runtime_lock_sha256",
    }
)


def _utc_today() -> str:
    return dt.datetime.now(dt.UTC).date().strftime("%Y%m%d")


def _shift_day(day: str, offset: int) -> str:
    value = dt.datetime.strptime(day, "%Y%m%d").date() + dt.timedelta(days=offset)
    return value.strftime("%Y%m%d")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def _load_freeze() -> tuple[dict[str, Any], str]:
    return forward.load_freeze(replay.FREEZE_PATH)


def _formal_start_ms(frozen: Mapping[str, Any]) -> float:
    try:
        start = dt.datetime.fromisoformat(str(frozen["formal_holdout_start"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Family37 formal holdout start is invalid") from exc
    safety = frozen.get("safety")
    if (
        start.tzinfo is None
        or start.astimezone(dt.UTC).strftime("%Y%m%d") != FIRST_COMPLETE_DAY
        or start.astimezone(dt.UTC).time() != dt.time(0, 0)
        or not isinstance(safety, Mapping)
        or safety.get("paper_only") is not True
        or safety.get("wallet_access") is not False
        or safety.get("production_orders") is not False
    ):
        raise ValueError("Family37 formal paper-only boundary drift")
    return start.timestamp() * 1_000.0


def _maximum_clock_offset_drift_ms(frozen: Mapping[str, Any]) -> float:
    execution = frozen.get("execution")
    if not isinstance(execution, Mapping):
        raise TypeError("Family37 frozen clock offset drift bound is invalid")
    try:
        value = float(execution["maximum_clock_offset_drift_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Family37 frozen clock offset drift bound is invalid") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Family37 frozen clock offset drift bound is invalid")
    return value


def _recorder_identities(
    frozen: Mapping[str, Any],
    freeze_sha256: str,
) -> dict[str, dict[str, Any]]:
    recorders = frozen.get("recorders")
    dependencies = frozen.get("dependencies_sha256")
    protocol = runtime.protocol_identity()
    if (
        not isinstance(recorders, Mapping)
        or set(recorders) != {"clob", "twap", "metadata"}
        or not isinstance(dependencies, Mapping)
        or protocol.get("protocol_freeze_sha256") != freeze_sha256
        or protocol.get("runtime_lock_sha256")
        != dependencies.get("settlement-lag-runtime-lock.json")
    ):
        raise ValueError("Family37 recorder protocol identity drift")
    output: dict[str, dict[str, Any]] = {}
    contract_fields = {
        "service",
        "unit_path",
        "unit_dependency",
        "process_executable_path",
        "process_executable_sha256",
        "process_cwd",
        "process_argv",
        "process_script_path",
        "script_dependency",
    }
    for name, raw in recorders.items():
        if not isinstance(raw, Mapping) or set(raw) != contract_fields:
            raise ValueError("Family37 recorder contract drift")
        unit_dependency = str(raw["unit_dependency"])
        script_dependency = str(raw["script_dependency"])
        if (
            unit_dependency not in dependencies
            or script_dependency not in dependencies
            or not isinstance(raw.get("process_argv"), list)
            or any(not isinstance(value, str) for value in raw["process_argv"])
        ):
            raise ValueError("Family37 recorder contract drift")
        output[str(name)] = {
            "recorder_service": str(raw["service"]),
            "recorder_unit_path": str(raw["unit_path"]),
            "recorder_unit_sha256": str(dependencies[unit_dependency]),
            "process_executable_path": str(raw["process_executable_path"]),
            "process_executable_sha256": str(raw["process_executable_sha256"]),
            "process_cwd": str(raw["process_cwd"]),
            "process_argv": list(raw["process_argv"]),
            "process_script_path": str(raw["process_script_path"]),
            "process_script_sha256": str(dependencies[script_dependency]),
            **protocol,
        }
    return output


def _validate_recorder_identity(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    if any(row.get(key) != expected.get(key) for key in RECORDER_IDENTITY_FIELDS):
        raise ValueError("Family37 recorder process identity drift")


def _supplemental_paths(root: Path, day: str) -> tuple[list[Path], list[Path], Path]:
    twap = [
        root / "twap" / f"settlement_twap.{_shift_day(day, -1)}T23.txt.gz",
        *(
            root / "twap" / f"settlement_twap.{day}T{hour:02d}.txt.gz"
            for hour in range(24)
        ),
    ]
    metadata = [
        root / "metadata" / f"settlement_meta.{metadata_day}.jsonl"
        for metadata_day in (_shift_day(day, -1), day)
    ]
    clob_clock = root / "clock" / f"settlement_clock.{day}.jsonl"
    expected = {*twap, *metadata, clob_clock}
    actual = (
        {path for path in root.rglob("*") if path.is_file()} if root.exists() else set()
    )
    if actual != expected or any(path.stat().st_size <= 0 for path in expected):
        raise ValueError("Family37 private supplemental input drift")
    return twap, metadata, clob_clock


def _snapshot_supplemental(
    source_twap_root: Path,
    source_metadata_root: Path,
    day_root: Path,
    day: str,
) -> tuple[list[Path], list[Path], Path]:
    destination = day_root / PRIVATE_SUPPLEMENTAL
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("Family37 private supplemental input drift")
        return _supplemental_paths(destination, day)

    source_twap = [
        source_twap_root
        / f"settlement_twap.{_shift_day(day, -1)}T23.txt.gz",
        *(
            source_twap_root / f"settlement_twap.{day}T{hour:02d}.txt.gz"
            for hour in range(24)
        ),
    ]
    source_metadata = [
        source_metadata_root / f"settlement_meta.{metadata_day}.jsonl"
        for metadata_day in (_shift_day(day, -1), day)
    ]
    source_clob_clock = source_metadata_root / f"settlement_clock.{day}.jsonl"
    sources = [*source_twap, *source_metadata, source_clob_clock]
    if any(not path.is_file() or path.stat().st_size <= 0 for path in sources):
        raise ValueError("Family37 requires 25 TWAP hours and two metadata days")

    before = {path.name: archive_daily._sha256(path) for path in sources}
    staging = day_root / f".{PRIVATE_SUPPLEMENTAL}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "twap").mkdir(parents=True)
    (staging / "metadata").mkdir()
    (staging / "clock").mkdir()
    try:
        for path in source_twap:
            shutil.copyfile(path, staging / "twap" / path.name)
        for path in source_metadata:
            shutil.copyfile(path, staging / "metadata" / path.name)
        shutil.copyfile(source_clob_clock, staging / "clock" / source_clob_clock.name)
        after = {path.name: archive_daily._sha256(path) for path in sources}
        copied = {
            path.name: archive_daily._sha256(path)
            for path in staging.rglob("*")
            if path.is_file()
        }
        if before != after or copied != before:
            raise ValueError("Family37 supplemental input changed during snapshot")
        staging.replace(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return _supplemental_paths(destination, day)


def _clock_identity(
    metadata_paths: Sequence[Path],
    day: str,
    *,
    recorder_identity: Mapping[str, Any],
) -> tuple[str, str, list[int]]:
    machine_ids: set[str] = set()
    boot_ids: set[str] = set()
    offsets: list[int] = []
    day_start = int(
        dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.UTC).timestamp()
    )
    expected_starts = set(range(day_start, day_start + 86_400, 300))
    observed_starts: set[int] = set()
    for path in metadata_paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path.name}:{line_number}: invalid execution metadata"
                    ) from exc
                if not isinstance(row, Mapping):
                    raise TypeError("Family37 metadata clock provenance drift")
                try:
                    start_ts = int(row["start_ts"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "Family37 metadata clock provenance drift"
                    ) from exc
                if start_ts not in expected_starts:
                    continue
                if (
                    row.get("schema") != replay.EXECUTION_METADATA_SCHEMA
                    or row.get("slug") != f"btc-updown-5m-{start_ts}"
                    or row.get("ntp_synchronized") is not True
                ):
                    raise ValueError("Family37 metadata clock provenance drift")
                _validate_recorder_identity(row, recorder_identity)
                machine_id = str(row.get("machine_id_sha256") or "")
                boot_id = str(row.get("boot_id") or "")
                if (
                    _SHA256_RE.fullmatch(machine_id) is None
                    or _BOOT_ID_RE.fullmatch(boot_id) is None
                ):
                    raise ValueError("Family37 metadata clock provenance drift")
                machine_ids.add(machine_id)
                boot_ids.add(boot_id)
                try:
                    offsets.append(int(row["realtime_minus_monotonic_ns"]))
                    sampled_ns = int(row["provenance_sampled_ns"])
                    recorded_ns = int(row["recorded_ns"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "Family37 metadata clock provenance drift"
                    ) from exc
                if sampled_ns <= 0 or recorded_ns <= 0:
                    raise ValueError("Family37 metadata clock provenance drift")
                observed_starts.add(start_ts)
    if observed_starts != expected_starts:
        raise ValueError("Family37 market clock coverage is incomplete")
    if len(machine_ids) != 1 or len(boot_ids) != 1:
        raise ValueError("Family37 inputs do not share one collector clock")
    return machine_ids.pop(), boot_ids.pop(), offsets


def _validate_twap_clock_provenance(
    paths: Sequence[Path],
    *,
    machine_id: str,
    boot_id: str,
    recorder_identity: Mapping[str, Any],
) -> tuple[list[int], set[float]]:
    offsets: list[int] = []
    payload_keys: set[float] = set()
    for path in paths:
        match = re.fullmatch(r"settlement_twap\.(\d{8}T\d{2})\.txt\.gz", path.name)
        if match is None:
            raise ValueError("Family37 TWAP segment identity drift")
        hour = dt.datetime.strptime(match.group(1), "%Y%m%dT%H").replace(tzinfo=dt.UTC)
        start_ns = int(hour.timestamp() * 1_000_000_000)
        end_ns = start_ns + 3_600_000_000_000
        active_epoch: int | None = None
        previous_ns = start_ns - 1
        first_kind: str | None = None
        last_kind: str | None = None
        twap_frames = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    clock, separator, text = line.rstrip("\n").partition("\t")
                    if not separator:
                        raise ValueError(
                            f"{path.name}:{line_number}: missing receipt timestamp"
                        )
                    try:
                        receive_ns = int(clock)
                        payload = json.loads(text)
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"{path.name}:{line_number}: invalid TWAP record"
                        ) from exc
                    if not start_ns <= receive_ns < end_ns or receive_ns < previous_ns:
                        raise ValueError("Family37 TWAP segment clock drift")
                    previous_ns = receive_ns
                    marker = (
                        payload.get("_recorder")
                        if isinstance(payload, Mapping)
                        else None
                    )
                    if isinstance(marker, Mapping):
                        if marker.get("schema") != "recorder-lifecycle-v1":
                            raise ValueError("Family37 TWAP lifecycle schema drift")
                        kind = str(marker.get("kind") or "")
                        try:
                            epoch = int(marker["epoch"])
                        except (KeyError, TypeError, ValueError) as exc:
                            raise ValueError(
                                "Family37 TWAP lifecycle epoch drift"
                            ) from exc
                        first_kind = first_kind or kind
                        last_kind = kind
                        if (
                            marker.get("source") != "rtds_cl"
                            or marker.get("machine_id_sha256") != machine_id
                            or marker.get("boot_id") != boot_id
                            or marker.get("ntp_synchronized") is not True
                        ):
                            raise ValueError("Family37 TWAP clock provenance drift")
                        _validate_recorder_identity(marker, recorder_identity)
                        try:
                            offsets.append(int(marker["realtime_minus_monotonic_ns"]))
                            sampled_ns = int(marker["provenance_sampled_ns"])
                        except (KeyError, TypeError, ValueError) as exc:
                            raise ValueError(
                                "Family37 TWAP clock provenance drift"
                            ) from exc
                        if sampled_ns <= 0:
                            raise ValueError("Family37 TWAP clock provenance drift")
                        if kind == "connection":
                            if active_epoch is not None:
                                raise ValueError("Family37 TWAP clock provenance drift")
                            active_epoch = epoch
                        elif kind == "disconnect":
                            if active_epoch != epoch:
                                raise ValueError("Family37 TWAP lifecycle epoch drift")
                            active_epoch = None
                        else:
                            raise ValueError("Family37 TWAP lifecycle kind drift")
                        continue
                    if active_epoch is None:
                        raise ValueError("Family37 TWAP frame outside connection epoch")
                    if isinstance(payload, Mapping) and (
                        payload.get("topic") == "crypto_prices_twap_sixty"
                    ):
                        point = replay.parse_twap_frame(
                            receive_ns / 1_000_000.0,
                            text,
                            receive_ns=receive_ns,
                        )
                        if point is not None:
                            twap_frames += 1
                            payload_keys.add(point.payload_ms)
        except (OSError, EOFError) as exc:
            raise ValueError("Family37 TWAP gzip integrity failure") from exc
        if (
            first_kind != "connection"
            or last_kind != "disconnect"
            or active_epoch is not None
            or twap_frames == 0
        ):
            raise ValueError("Family37 TWAP segment is not a closed usable epoch")
    return offsets, payload_keys


def _validate_market_and_twap_coverage(
    artifact: Path,
    day: str,
    payload_keys: set[float],
) -> None:
    day_start = int(
        dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.UTC).timestamp()
    )
    expected_slots = list(range(day_start, day_start + 86_400, 300))
    mappings = list(
        h_replay_archive.iter_market_mappings(
            artifact / "market_registry.csv.gz"
        )
    )
    slots = [int(row["slot"]) for row in mappings]
    market_ids = [str(row["market_id"]) for row in mappings]
    if (
        slots != expected_slots
        or len(set(slots)) != 288
        or len(set(market_ids)) != 288
    ):
        raise ValueError("Family37 strict market registry is not the exact UTC day")
    required: set[float] = set()
    for slot in expected_slots:
        required.update(
            {
                slot * 1_000.0 - 2_000.0,
                (slot + 300) * 1_000.0 - 3_000.0,
                (slot + 300) * 1_000.0 - 2_000.0,
            }
        )
    missing = required - payload_keys
    if missing:
        raise ValueError(
            "Family37 TWAP payload coverage is incomplete: "
            f"{len(missing)} required keys absent"
        )


def _validate_offset_drift(
    offsets: Sequence[int], maximum_offset_drift_ms: float
) -> None:
    if (
        not offsets
        or max(offsets) - min(offsets) > maximum_offset_drift_ms * 1_000_000.0
    ):
        raise ValueError(
            "Family37 collector clock offset drift exceeded the frozen bound"
        )


def _portable_file_identity(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": archive_daily._sha256(path),
    }


def _validate_clob_clock_provenance(
    path: Path,
    archive_snapshot_path: Path,
    artifact: Path,
    day: str,
    *,
    machine_id: str,
    boot_id: str,
    recorder_identity: Mapping[str, Any],
) -> tuple[list[int], dict[str, Any]]:
    snapshot = _read_json(archive_snapshot_path, "strict archive snapshot")
    rec = snapshot.get("rec")
    poly = snapshot.get("poly")
    if not isinstance(rec, Mapping) or not isinstance(poly, Mapping):
        raise TypeError("strict archive snapshot identity drift")
    archive_daily.validate_archive_manifest(rec, day, "rec")
    archive_daily.validate_archive_manifest(poly, day, "poly")
    raw_identity = shared_daily.strict_prebuild.raw_archive_identity(rec, poly)
    binding_path = artifact / archive_daily.ARTIFACT_BINDING
    binding = _read_json(binding_path, "strict input binding")
    if binding.get("raw_archive_manifests_sha256") != raw_identity:
        raise ValueError("strict CLOB artifact custody binding drift")

    archive_files = {
        str(row["file"]): row
        for row in poly["files"]
        if isinstance(row, Mapping)
        and re.fullmatch(
            rf"poly_clob\.{day}T\d{{2}}\.jsonl\.gz", str(row.get("file") or "")
        )
    }
    expected_hours = {f"{day}T{hour:02d}" for hour in range(24)}
    expected_evidence = {
        (hour, kind)
        for hour in expected_hours
        for kind in ("active_witness", "closed_identity")
    }
    observed: set[tuple[str, str]] = set()
    active_processes: dict[int, tuple[int, str, int]] = {}
    active_cmdlines: set[str] = set()
    active_clock_ticks: set[int] = set()
    offsets: list[int] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path.name}:{line_number}: invalid CLOB clock sidecar"
                ) from exc
            if not isinstance(row, Mapping):
                raise TypeError("CLOB clock sidecar row must be an object")
            hour = str(row.get("target_hour") or "")
            evidence_kind = str(row.get("evidence_kind") or "")
            evidence_key = (hour, evidence_kind)
            filename = f"poly_clob.{hour}.jsonl.gz"
            archive_row = archive_files.get(filename)
            if (
                hour not in expected_hours
                or evidence_key not in expected_evidence
                or evidence_key in observed
                or row.get("schema") != CLOB_CLOCK_SCHEMA
                or row.get("clob_file") != filename
                or archive_row is None
            ):
                raise ValueError("Family37 CLOB clock sidecar archive binding drift")
            if (
                row.get("machine_id_sha256") != machine_id
                or row.get("boot_id") != boot_id
                or row.get("ntp_synchronized") is not True
            ):
                raise ValueError("Family37 CLOB clock sidecar provenance drift")
            try:
                offset = int(row["realtime_minus_monotonic_ns"])
                sampled_ns = int(row["provenance_sampled_ns"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Family37 CLOB clock sidecar provenance drift"
                ) from exc
            hour_start_ns = int(
                dt.datetime.strptime(hour, "%Y%m%dT%H")
                .replace(tzinfo=dt.UTC)
                .timestamp()
                * 1_000_000_000
            )
            hour_end_ns = hour_start_ns + 3_600_000_000_000
            if evidence_kind == "active_witness":
                _validate_recorder_identity(row, recorder_identity)
                try:
                    pid = int(row["main_pid"])
                    starttime_ticks = int(row["process_starttime_ticks"])
                    clock_ticks = int(row["clock_ticks_per_second"])
                    file_dev = int(row["active_file_dev"])
                    file_inode = int(row["active_file_inode"])
                    open_fd = int(row["open_fd"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "Family37 CLOB active process identity drift"
                    ) from exc
                cmdline_hash = str(row.get("process_cmdline_sha256") or "")
                monotonic_ns = sampled_ns - offset
                if (
                    row.get("service") != CLOB_RECORDER_SERVICE
                    or pid <= 0
                    or pid > 2**31 - 1
                    or starttime_ticks <= 0
                    or clock_ticks <= 0
                    or clock_ticks > 1_000_000
                    or file_dev < 0
                    or file_inode <= 0
                    or open_fd < 0
                    or _SHA256_RE.fullmatch(cmdline_hash) is None
                    or monotonic_ns <= 0
                    or starttime_ticks * 1_000_000_000 > monotonic_ns * clock_ticks
                ):
                    raise ValueError("Family37 CLOB active process identity drift")
                identity = (starttime_ticks, cmdline_hash, clock_ticks)
                previous_identity = active_processes.setdefault(pid, identity)
                if previous_identity != identity:
                    raise ValueError("Family37 CLOB active process identity drift")
                active_cmdlines.add(cmdline_hash)
                active_clock_ticks.add(clock_ticks)
                if not hour_start_ns <= sampled_ns < hour_end_ns:
                    raise ValueError("Family37 CLOB active witness sampling time drift")
            else:
                try:
                    byte_count = int(row["bytes"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "Family37 CLOB clock sidecar archive binding drift"
                    ) from exc
                if (
                    byte_count != int(archive_row["size"])
                    or row.get("sha256") != archive_row["sha256"]
                ):
                    raise ValueError(
                        "Family37 CLOB clock sidecar archive binding drift"
                    )
                if not (
                    hour_end_ns
                    <= sampled_ns
                    <= hour_end_ns + MAXIMUM_CLOB_CUSTODY_LAG_S * 1_000_000_000
                ):
                    raise ValueError("Family37 CLOB closed identity sampling lag drift")
            offsets.append(offset)
            observed.add(evidence_key)
    if observed != expected_evidence:
        raise ValueError("Family37 CLOB active/closed hour coverage is incomplete")
    if len(active_cmdlines) != 1 or len(active_clock_ticks) != 1:
        raise ValueError("Family37 CLOB active process identity drift")
    return offsets, {
        "scope": CLOB_CLOCK_SCOPE,
        "hours": len(expected_hours),
        "active_witnesses": sum(kind == "active_witness" for _, kind in observed),
        "closed_identities": sum(kind == "closed_identity" for _, kind in observed),
        "sidecar": _portable_file_identity(path),
        "archive_snapshot": _portable_file_identity(archive_snapshot_path),
        "strict_input_binding": _portable_file_identity(binding_path),
        "raw_archive_manifests_sha256": raw_identity,
    }


def _write_json_or_compare(path: Path, payload: Mapping[str, Any], label: str) -> None:
    shared_daily._write_json_or_compare(path, payload, label)


def _bind_inputs(
    day_root: Path,
    artifact: Path,
    twap_paths: Sequence[Path],
    metadata_paths: Sequence[Path],
    clob_clock_path: Path,
    day: str,
    *,
    maximum_offset_drift_ms: float,
    recorder_identities: Mapping[str, Mapping[str, Any]],
) -> tuple[Path, Path, dict[str, Any]]:
    machine_id, boot_id, metadata_offsets = _clock_identity(
        metadata_paths,
        day,
        recorder_identity=recorder_identities["metadata"],
    )
    twap_offsets, twap_payload_keys = _validate_twap_clock_provenance(
        twap_paths,
        machine_id=machine_id,
        boot_id=boot_id,
        recorder_identity=recorder_identities["twap"],
    )
    _validate_market_and_twap_coverage(artifact, day, twap_payload_keys)
    clob_offsets, custody = _validate_clob_clock_provenance(
        clob_clock_path,
        day_root / "archive-manifests.json",
        artifact,
        day,
        machine_id=machine_id,
        boot_id=boot_id,
        recorder_identity=recorder_identities["clob"],
    )
    _validate_offset_drift(
        [*metadata_offsets, *twap_offsets, *clob_offsets], maximum_offset_drift_ms
    )
    attestation_path = day_root / CLOCK_ATTESTATION
    attestation = replay.clock_attestation_payload(
        twap_paths,
        artifact,
        metadata_paths,
        machine_id_sha256=machine_id,
        boot_id=boot_id,
        ntp_synchronized=True,
        clob_custody=custody,
    )
    _write_json_or_compare(
        attestation_path, attestation, "Family37 input clock attestation"
    )
    manifest_path = day_root / INPUT_MANIFEST
    manifest = replay.build_standard_input_manifest(
        twap_paths, artifact, metadata_paths, attestation_path
    )
    _write_json_or_compare(manifest_path, manifest, "Family37 input manifest")
    return attestation_path, manifest_path, manifest


def _load_or_persist_inputs(
    strict_root: Path,
    twap_root: Path,
    metadata_root: Path,
    day_root: Path,
    day: str,
    formal_start_ms: float,
    maximum_offset_drift_ms: float,
    recorder_identities: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    strict = shared_daily._load_or_persist_input(
        strict_root, day_root, day, formal_start_ms
    )
    twap_paths, metadata_paths, clob_clock_path = _snapshot_supplemental(
        twap_root, metadata_root, day_root, day
    )
    attestation, manifest_path, manifest = _bind_inputs(
        day_root,
        Path(strict["artifact"]),
        twap_paths,
        metadata_paths,
        clob_clock_path,
        day,
        maximum_offset_drift_ms=maximum_offset_drift_ms,
        recorder_identities=recorder_identities,
    )
    return {
        **strict,
        "twap_paths": twap_paths,
        "metadata_paths": metadata_paths,
        "clob_clock_path": clob_clock_path,
        "clock_attestation_path": attestation,
        "input_manifest_path": manifest_path,
        "input_manifest": manifest,
    }


def _input_fingerprint(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **shared_daily._input_fingerprint(identity),
        "clock_attestation_sha256": archive_daily._sha256(
            Path(identity["clock_attestation_path"])
        ),
        "input_manifest_sha256": archive_daily._sha256(
            Path(identity["input_manifest_path"])
        ),
        "clob_clock_sidecar_sha256": archive_daily._sha256(
            Path(identity["clob_clock_path"])
        ),
    }


def _persist_replay(day_root: Path, identity: Mapping[str, Any]) -> tuple[Path, Path]:
    result = replay.run_standard_artifact(
        identity["artifact"],
        identity["twap_paths"],
        identity["metadata_paths"],
        identity["clock_attestation_path"],
        identity["input_manifest"],
        mode="formal",
    )
    if (
        result.get("sample_mode") != "formal"
        or result.get("formal_input_bound") is not True
        or not isinstance(result.get("artifact"), Mapping)
        or result["artifact"].get("run_id") != identity["run_id"]
    ):
        raise ValueError("Family37 replay is not eligible for formal admission")
    grouped = result.get("rows")
    variants = (
        replay.FINAL_VARIANT,
        replay.PREFINAL_VARIANT,
        replay.WRONG_SIDE_VARIANT,
    )
    if not isinstance(grouped, Mapping) or set(grouped) != set(variants):
        raise ValueError("Family37 replay row arms drift")
    rows: list[dict[str, Any]] = []
    for variant in variants:
        arm = grouped[variant]
        if not isinstance(arm, list) or any(
            not isinstance(row, Mapping) for row in arm
        ):
            raise TypeError("Family37 replay rows must be objects")
        if any(row.get("variant", variant) != variant for row in arm):
            raise ValueError("Family37 replay row variant drift")
        rows.extend(dict(row) for row in arm)

    rows_path = day_root / REPLAY_ROWS
    pending_rows = day_root / f".{REPLAY_ROWS}.pending"
    source_confirmation._atomic_write_rows(pending_rows, rows)
    if rows_path.is_file():
        if archive_daily._sha256(rows_path) != archive_daily._sha256(pending_rows):
            pending_rows.unlink()
            raise ValueError("persisted Family37 replay rows drift")
        pending_rows.unlink()
    else:
        pending_rows.replace(rows_path)

    summary = {key: value for key, value in result.items() if key != "rows"}
    summary["record_count"] = len(rows)
    summary_path = day_root / REPLAY_SUMMARY
    _write_json_or_compare(summary_path, summary, "Family37 replay summary")
    return summary_path, rows_path


def _state_hashes(root: Path) -> dict[str, str | None]:
    return {
        name: archive_daily._sha256(root / name) if (root / name).is_file() else None
        for name in STATE_FILES
    }


def _read_admission(
    state: Path, freeze_sha256: str, *, validate_state: bool = True
) -> dict[str, Any] | None:
    path = state / ADMISSION
    if not path.is_file():
        return None
    payload = _read_json(path, "Family37 admission")
    hashes = payload.get("state_sha256")
    if (
        payload.get("schema") != "settlement-lag-admission-v1"
        or payload.get("freeze_sha256") != freeze_sha256
        or not re.fullmatch(r"\d{8}", str(payload.get("day") or ""))
        or str(payload.get("day")) < FIRST_COMPLETE_DAY
        or _SHA256_RE.fullmatch(str(payload.get("complete_sha256") or "")) is None
        or not isinstance(hashes, Mapping)
        or set(hashes) != set(STATE_FILES)
        or any(
            digest is not None and _SHA256_RE.fullmatch(str(digest)) is None
            for digest in hashes.values()
        )
    ):
        raise ValueError("Family37 admission identity drift")
    if validate_state and _state_hashes(state) != dict(hashes):
        raise ValueError("Family37 admitted state hash mismatch")
    return payload


def next_expected_day(state_dir: str | Path, freeze_sha256: str) -> str:
    """Return the oldest Family37 day not yet admitted."""
    admission = _read_admission(
        Path(state_dir),
        freeze_sha256,
        validate_state=False,
    )
    return (
        FIRST_COMPLETE_DAY
        if admission is None
        else _shift_day(str(admission["day"]), 1)
    )


def _validate_interrupted_transition(
    state: Path,
    admission: Mapping[str, Any],
    complete_path: Path,
    day: str,
    freeze_sha256: str,
) -> None:
    complete = _read_json(complete_path, "Family37 recovery journal")
    base = complete.get("base_state_sha256")
    target = complete.get("target_state_sha256")
    admitted = admission["state_sha256"]
    current = _state_hashes(state)
    if (
        admission["state_sha256"].get(VERDICT) is not None
        or complete.get("schema") != "settlement-lag-complete-day-v1"
        or complete.get("status") != "complete"
        or complete.get("day") != day
        or complete.get("freeze_sha256") != freeze_sha256
        or not isinstance(base, Mapping)
        or not isinstance(target, Mapping)
        or dict(base) != dict(admitted)
        or set(target) != set(STATE_FILES)
        or any(
            digest is not None and _SHA256_RE.fullmatch(str(digest)) is None
            for digest in target.values()
        )
        or any(current[name] not in (base[name], target[name]) for name in STATE_FILES)
    ):
        raise ValueError("Family37 admitted state hash mismatch")


def _stage_state(state: Path, day_root: Path) -> Path:
    staged = day_root / "state-next"
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    for name in STATE_FILES:
        source = state / name
        if source.is_file():
            shutil.copyfile(source, staged / name)
    return staged


def _evidence_row(path: Path, day_root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(day_root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": archive_daily._sha256(path),
    }


def _expected_evidence_paths(
    day: str, target_state: Mapping[str, str | None]
) -> set[str]:
    return {
        "archive-manifests.json",
        CLOCK_ATTESTATION,
        INPUT_MANIFEST,
        REPLAY_ROWS,
        REPLAY_SUMMARY,
        *(f"{PRIVATE_ARTIFACT}/{name}" for name in shared_daily.INPUT_ARTIFACT_FILES),
        f"{PRIVATE_SUPPLEMENTAL}/twap/settlement_twap.{_shift_day(day, -1)}T23.txt.gz",
        *(
            f"{PRIVATE_SUPPLEMENTAL}/twap/settlement_twap.{day}T{hour:02d}.txt.gz"
            for hour in range(24)
        ),
        *(
            f"{PRIVATE_SUPPLEMENTAL}/metadata/settlement_meta.{metadata_day}.jsonl"
            for metadata_day in (_shift_day(day, -1), day)
        ),
        f"{PRIVATE_SUPPLEMENTAL}/clock/settlement_clock.{day}.jsonl",
        *(
            f"state-next/{name}"
            for name, digest in target_state.items()
            if digest is not None
        ),
    }


def _validate_complete(
    complete: Mapping[str, Any],
    *,
    day: str,
    freeze_sha256: str,
    identity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    fingerprint = _input_fingerprint(identity)
    if (
        complete.get("schema") != "settlement-lag-complete-day-v1"
        or complete.get("status") != "complete"
        or complete.get("day") != day
        or complete.get("freeze_sha256") != freeze_sha256
        or any(complete.get(name) != value for name, value in fingerprint.items())
    ):
        raise ValueError("completed Family37 day input or freeze drift")
    base = complete.get("base_state_sha256")
    target = complete.get("target_state_sha256")
    if (
        not isinstance(base, Mapping)
        or not isinstance(target, Mapping)
        or set(base) != set(STATE_FILES)
        or set(target) != set(STATE_FILES)
        or any(
            digest is not None and _SHA256_RE.fullmatch(str(digest)) is None
            for hashes in (base, target)
            for digest in hashes.values()
        )
    ):
        raise ValueError("completed Family37 state hash drift")
    evidence = complete.get("evidence")
    if not isinstance(evidence, list):
        raise TypeError("completed Family37 evidence must be an array")
    rows: list[dict[str, Any]] = []
    paths: list[str] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            raise TypeError("completed Family37 evidence item must be an object")
        relative = Path(str(item.get("path") or ""))
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or not isinstance(item.get("bytes"), int)
            or int(item["bytes"]) < 0
            or _SHA256_RE.fullmatch(str(item.get("sha256") or "")) is None
        ):
            raise ValueError("completed Family37 evidence drift")
        rows.append(dict(item))
        paths.append(relative.as_posix())
    expected = _expected_evidence_paths(day, target)
    if set(paths) != expected or len(paths) != len(expected):
        raise ValueError("completed Family37 evidence set drift")
    return rows


def _verify_local_evidence(
    day_root: Path, evidence_rows: Sequence[Mapping[str, Any]]
) -> list[Path]:
    paths: list[Path] = []
    for item in evidence_rows:
        path = day_root / str(item["path"])
        if (
            not path.is_file()
            or path.stat().st_size != int(item["bytes"])
            or archive_daily._sha256(path) != item["sha256"]
        ):
            raise ValueError("completed Family37 evidence changed")
        paths.append(path)
    return paths


def _upload_evidence(
    s3: Any, bucket: str, day: str, day_root: Path, files: Sequence[Path]
) -> None:
    for path in files:
        relative = path.relative_to(day_root).as_posix()
        archive_daily._upload_file(
            s3, bucket, f"{EVIDENCE_PREFIX}/{day}/{relative}", path
        )


def _commit_completed_day(
    complete: Mapping[str, Any], complete_path: Path, state: Path, day_root: Path
) -> None:
    base = dict(complete["base_state_sha256"])
    target = dict(complete["target_state_sha256"])
    current = _state_hashes(state)
    for name in STATE_FILES:
        if current[name] not in (base[name], target[name]):
            raise ValueError(f"Family37 state changed while committing {name}")
    state.mkdir(parents=True, exist_ok=True)
    staged = day_root / "state-next"
    for name in STATE_FILES:
        digest = target[name]
        destination = state / name
        if digest is None:
            if destination.exists():
                destination.unlink()
            continue
        source = staged / name
        if not source.is_file() or archive_daily._sha256(source) != digest:
            raise ValueError(f"staged Family37 state changed: {name}")
        temporary = state / f".{name}.tmp"
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    if _state_hashes(state) != target:
        raise ValueError("Family37 state commit verification failed")
    archive_daily._atomic_json(
        state / ADMISSION,
        {
            "schema": "settlement-lag-admission-v1",
            "day": complete["day"],
            "freeze_sha256": complete["freeze_sha256"],
            "complete_sha256": archive_daily._sha256(complete_path),
            "state_sha256": target,
        },
    )


def _admitted_terminal(
    state: Path, admission: Mapping[str, Any]
) -> dict[str, Any] | None:
    expected = admission["state_sha256"][VERDICT]
    if expected is None:
        return None
    verdict_path = state / VERDICT
    if not verdict_path.is_file() or archive_daily._sha256(verdict_path) != expected:
        raise ValueError("Family37 admitted state hash mismatch")
    verdict = _read_json(verdict_path, "Family37 verdict")
    if verdict.get("status") == "collecting":
        raise ValueError("Family37 verdict is not terminal")
    return {"status": "terminal", "verdict": verdict}


def process_day(
    day: str,
    strict_root: str | Path,
    twap_root: str | Path,
    metadata_root: str | Path,
    work_root: str | Path,
    state_dir: str | Path,
    *,
    s3: Any,
    bucket: str = BUCKET,
) -> dict[str, Any]:
    frozen, freeze_sha256 = _load_freeze()
    formal_start_ms = _formal_start_ms(frozen)
    maximum_offset_drift_ms = _maximum_clock_offset_drift_ms(frozen)
    recorder_identities = _recorder_identities(frozen, freeze_sha256)
    if not re.fullmatch(r"\d{8}", day) or day < FIRST_COMPLETE_DAY:
        raise ValueError("day is outside the frozen Family37 sample")
    if day >= _utc_today():
        raise ValueError("Family37 admission requires a closed UTC day")

    strict = Path(strict_root)
    twap = Path(twap_root)
    metadata = Path(metadata_root)
    work = Path(work_root)
    state = Path(state_dir)
    day_root = work / day
    complete_path = day_root / "complete.json"
    admission = _read_admission(
        state, freeze_sha256, validate_state=not complete_path.is_file()
    )
    if admission is not None and day != admission["day"]:
        if _state_hashes(state) == dict(admission["state_sha256"]):
            terminal = _admitted_terminal(state, admission)
            if terminal is not None:
                return terminal
        elif complete_path.is_file():
            _validate_interrupted_transition(
                state, admission, complete_path, day, freeze_sha256
            )
        else:
            raise ValueError("Family37 admitted state hash mismatch")
    if admission is None:
        if (
            any((state / name).exists() for name in STATE_FILES)
            and not complete_path.is_file()
        ):
            raise ValueError("Family37 state exists without admission")
        expected_day = FIRST_COMPLETE_DAY
    else:
        admitted_day = str(admission["day"])
        if day == admitted_day:
            if not complete_path.is_file():
                raise ValueError("admitted Family37 day lacks completion journal")
            expected_day = admitted_day
        else:
            expected_day = _shift_day(admitted_day, 1)
    if day != expected_day:
        raise ValueError(
            f"day is not the next Family37 admission: expected {expected_day}"
        )

    if complete_path.is_file():
        identity = _load_or_persist_inputs(
            strict,
            twap,
            metadata,
            day_root,
            day,
            formal_start_ms,
            maximum_offset_drift_ms,
            recorder_identities,
        )
        complete = _read_json(complete_path, "completed Family37 day")
        evidence_rows = _validate_complete(
            complete,
            day=day,
            freeze_sha256=freeze_sha256,
            identity=identity,
        )
        evidence = _verify_local_evidence(day_root, evidence_rows)
        if admission is not None and day == admission["day"]:
            if admission["complete_sha256"] != archive_daily._sha256(complete_path):
                raise ValueError("admitted Family37 complete hash mismatch")
            _read_admission(state, freeze_sha256, validate_state=True)
            return complete
        _upload_evidence(s3, bucket, day, day_root, [*evidence, complete_path])
        _commit_completed_day(complete, complete_path, state, day_root)
        return complete

    day_root.mkdir(parents=True, exist_ok=True)
    identity = _load_or_persist_inputs(
        strict,
        twap,
        metadata,
        day_root,
        day,
        formal_start_ms,
        maximum_offset_drift_ms,
        recorder_identities,
    )
    initial_fingerprint = _input_fingerprint(identity)
    summary_path, rows_path = _persist_replay(day_root, identity)
    checked = _load_or_persist_inputs(
        strict,
        twap,
        metadata,
        day_root,
        day,
        formal_start_ms,
        maximum_offset_drift_ms,
        recorder_identities,
    )
    if _input_fingerprint(checked) != initial_fingerprint:
        raise ValueError("Family37 daily input drifted during replay")

    base_state_hashes = _state_hashes(state)
    staged = _stage_state(state, day_root)
    result = forward.accumulate(
        h_forward._read_jsonl(rows_path),
        frozen,
        freeze_sha256,
        ledger_path=staged / OBSERVATIONS,
        status_path=staged / STATUS,
        verdict_path=staged / VERDICT,
    )
    if _state_hashes(state) != base_state_hashes:
        raise ValueError("Family37 base state changed while staging")
    target_state_hashes = _state_hashes(staged)
    evidence = [
        day_root / "archive-manifests.json",
        day_root / CLOCK_ATTESTATION,
        day_root / INPUT_MANIFEST,
        summary_path,
        rows_path,
        *(
            day_root / PRIVATE_ARTIFACT / name
            for name in shared_daily.INPUT_ARTIFACT_FILES
        ),
        *checked["twap_paths"],
        *checked["metadata_paths"],
        checked["clob_clock_path"],
        *(
            staged / name
            for name, digest in target_state_hashes.items()
            if digest is not None
        ),
    ]
    complete = {
        "schema": "settlement-lag-complete-day-v1",
        "status": "complete",
        "day": day,
        "freeze_sha256": freeze_sha256,
        **initial_fingerprint,
        "replay_summary_sha256": archive_daily._sha256(summary_path),
        "replay_rows_sha256": archive_daily._sha256(rows_path),
        "forward_status": result,
        "base_state_sha256": base_state_hashes,
        "target_state_sha256": target_state_hashes,
        "evidence": [_evidence_row(path, day_root) for path in evidence],
    }
    archive_daily._atomic_json(complete_path, complete)
    _upload_evidence(s3, bucket, day, day_root, [*evidence, complete_path])
    _commit_completed_day(complete, complete_path, state, day_root)
    return complete


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day")
    parser.add_argument("--strict-root", default=DEFAULT_STRICT_ROOT)
    parser.add_argument("--twap-root", default=DEFAULT_TWAP_ROOT)
    parser.add_argument("--metadata-root", default=DEFAULT_METADATA_ROOT)
    parser.add_argument("--work", default=DEFAULT_WORK_ROOT)
    parser.add_argument("--state", default=DEFAULT_STATE_ROOT)
    parser.add_argument("--bucket", default=BUCKET)
    args = parser.parse_args()

    _frozen, freeze_sha256 = _load_freeze()
    state = Path(args.state)
    if args.day is None:
        args.day = next_expected_day(state, freeze_sha256)
    if re.fullmatch(r"\d{8}", args.day) and args.day >= _utc_today():
        print(
            json.dumps(
                {"status": "skipped", "day": args.day, "reason": "UTC_day_not_closed"},
                sort_keys=True,
            )
        )
        return
    state.mkdir(parents=True, exist_ok=True)
    with (state / ".daily.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = process_day(
            args.day,
            args.strict_root,
            args.twap_root,
            args.metadata_root,
            args.work,
            args.state,
            s3=boto3.client("s3", region_name="eu-west-1"),
            bucket=args.bucket,
        )
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
