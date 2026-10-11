"""Record the execution contract for BTC five-minute markets without touching order flow."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import settlement_lag_runtime as runtime

GAMMA = "https://gamma-api.polymarket.com"
SCHEMA = "settlement-lag-market-metadata-v2"
CLOB_CLOCK_SCHEMA = "settlement-lag-clob-clock-sidecar-v3"
DEFAULT_CLOB_ROOT = "/home/ubuntu/rec/data_poly"
DEFAULT_CLOB_SERVICE = "poly-clob-recorder.service"
DEFAULT_PROC_ROOT = "/proc"
MAXIMUM_CLOSE_AGE_S = 600
META_SERVICE = "settlement-lag-meta-recorder.service"
META_UNIT_PATH = "/etc/systemd/system/settlement-lag-meta-recorder.service"


def market_slugs(now: float) -> list[str]:
    start = int(now) // 300 * 300
    return [f"btc-updown-5m-{slot}" for slot in (start, start + 300)]


def _list(value: object) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise TypeError("market metadata list field is invalid")
    return value


def market_metadata(
    slug: str,
    market: Mapping[str, Any],
    *,
    recorded_ns: int,
    host_provenance: Mapping[str, Any] | None = None,
    recorder_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fee = market.get("feeSchedule")
    if not isinstance(fee, Mapping):
        raise TypeError("market fee schedule is absent")
    tokens = [str(value) for value in _list(market["clobTokenIds"])]
    outcomes = [str(value) for value in _list(market["outcomes"])]
    if len(tokens) != 2 or len(outcomes) != 2 or set(outcomes) != {"Up", "Down"}:
        raise ValueError("market token mapping is invalid")
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "recorded_ns": int(recorded_ns),
        "slug": str(slug),
        "start_ts": int(str(slug).rsplit("-", 1)[1]),
        "market_id": str(market["conditionId"]),
        "up_token_id": tokens[outcomes.index("Up")],
        "down_token_id": tokens[outcomes.index("Down")],
        "tick_size": float(market["orderPriceMinTickSize"]),
        "min_size": float(market["orderMinSize"]),
        "fee_rate": float(fee["rate"]),
        "fee_exponent": float(fee["exponent"]),
        "taker_only_fee": bool(fee["takerOnly"]),
        "fees_enabled": bool(market["feesEnabled"]),
    }
    numeric = (
        row["tick_size"],
        row["min_size"],
        row["fee_rate"],
        row["fee_exponent"],
    )
    if any(value <= 0 for value in numeric):
        raise ValueError("market execution metadata is non-positive")
    if host_provenance is not None:
        row.update(host_provenance)
    if recorder_identity is not None:
        row.update(recorder_identity)
    return row


def capture_current_recorder_identity() -> dict[str, Any]:
    return runtime.current_process_identity(
        service=META_SERVICE,
        unit_path=META_UNIT_PATH,
        script_path=Path(__file__),
    )


def capture_host_provenance() -> dict[str, Any]:
    machine_id = Path("/etc/machine-id").read_text(encoding="ascii").strip()
    boot_id = (
        Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    )
    synchronized = (
        subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        .stdout.strip()
        .lower()
        == "yes"
    )
    monotonic_before_ns = time.monotonic_ns()
    realtime_ns = time.time_ns()
    monotonic_after_ns = time.monotonic_ns()
    return {
        "machine_id_sha256": hashlib.sha256(machine_id.encode()).hexdigest(),
        "boot_id": boot_id,
        "ntp_synchronized": synchronized,
        "realtime_minus_monotonic_ns": realtime_ns
        - (monotonic_before_ns + monotonic_after_ns) // 2,
        "provenance_sampled_ns": realtime_ns,
    }


def fetch_market(slug: str) -> dict[str, Any]:
    url = f"{GAMMA}/events?" + urllib.parse.urlencode({"slug": slug})
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "settlement-lag-metadata-recorder/1.0"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        events = json.load(response)
    if not isinstance(events, list) or not events:
        raise ValueError(f"Gamma returned no event for {slug}")
    markets = events[0].get("markets")
    if not isinstance(markets, list) or not markets:
        raise ValueError(f"Gamma returned no market for {slug}")
    return market_metadata(
        slug,
        markets[0],
        recorded_ns=time.time_ns(),
        host_provenance=capture_host_provenance(),
        recorder_identity=capture_current_recorder_identity(),
    )


def _stable_file_identity(path: Path) -> tuple[int, str]:
    before = path.stat()
    if before.st_size <= 0:
        raise ValueError("closed CLOB hour is empty")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    after = path.stat()
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity:
        raise ValueError("closed CLOB hour changed while hashing")
    return after.st_size, digest.hexdigest()


def _hour_bounds_ns(hour: str) -> tuple[int, int]:
    if re.fullmatch(r"\d{8}T\d{2}", hour) is None:
        raise ValueError("CLOB clock hour must be YYYYMMDDTHH")
    try:
        start = dt.datetime.strptime(hour, "%Y%m%dT%H").replace(tzinfo=dt.UTC)
    except ValueError as exc:
        raise ValueError("CLOB clock hour must be YYYYMMDDTHH") from exc
    start_ns = int(start.timestamp() * 1_000_000_000)
    return start_ns, start_ns + 3_600_000_000_000


def _read_clock_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path.name}:{line_number}: invalid CLOB clock sidecar"
                ) from exc
            if not isinstance(row, dict):
                raise TypeError("CLOB clock sidecar row must be an object")
            rows.append(row)
    return rows


def _clock_path(output_root: str | Path, hour: str) -> Path:
    destination = Path(output_root)
    destination.mkdir(parents=True, exist_ok=True)
    return destination / f"settlement_clock.{hour[:8]}.jsonl"


def _existing_clock_row(
    path: Path, hour: str, evidence_kind: str
) -> dict[str, Any] | None:
    existing = [
        row
        for row in _read_clock_rows(path)
        if row.get("target_hour") == hour and row.get("evidence_kind") == evidence_kind
    ]
    if len(existing) > 1:
        raise ValueError("CLOB clock sidecar drift")
    return existing[0] if existing else None


def _append_clock_row(path: Path, row: Mapping[str, Any]) -> None:
    line = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, (line + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _service_main_pid(service_name: str) -> int:
    result = subprocess.run(
        ["systemctl", "show", "--property=MainPID", "--value", service_name],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    try:
        pid = int(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("CLOB recorder MainPID is invalid") from exc
    if pid <= 0:
        raise ValueError("CLOB recorder MainPID is absent")
    return pid


def _service_fragment_path(service_name: str) -> Path:
    result = subprocess.run(
        ["systemctl", "show", "--property=FragmentPath", "--value", service_name],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    path = Path(result.stdout.strip())
    if not path.is_absolute() or not path.is_file():
        raise ValueError("CLOB recorder service unit is invalid")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _external_process_identity(
    process_root: Path,
    cmdline: bytes,
    service_name: str,
) -> dict[str, Any]:
    argv = [part.decode("utf-8") for part in cmdline.rstrip(b"\0").split(b"\0")]
    if len(argv) < 2 or any(not value for value in argv):
        raise ValueError("CLOB recorder argv is invalid")
    try:
        executable = (process_root / "exe").resolve(strict=True)
        cwd = (process_root / "cwd").resolve(strict=True)
    except OSError as exc:
        raise ValueError("CLOB recorder process paths are invalid") from exc
    script = Path(argv[1])
    if not script.is_absolute():
        script = cwd / script
    script = script.resolve(strict=True)
    unit = _service_fragment_path(service_name).resolve(strict=True)
    return {
        "recorder_service": service_name,
        "recorder_unit_path": str(unit),
        "recorder_unit_sha256": _sha256_file(unit),
        "process_executable_path": str(executable),
        "process_executable_sha256": _sha256_file(executable),
        "process_cwd": str(cwd),
        "process_argv": argv,
        "process_script_path": str(script),
        "process_script_sha256": _sha256_file(script),
        **runtime.protocol_identity(),
    }


def _process_identity(process_root: Path) -> tuple[int, bytes]:
    stat = (process_root / "stat").read_text(encoding="utf-8").strip()
    close = stat.rfind(")")
    fields = stat[close + 2 :].split() if close >= 0 else []
    if len(fields) <= 19:
        raise ValueError("CLOB recorder process stat is invalid")
    try:
        starttime_ticks = int(fields[19])
    except ValueError as exc:
        raise ValueError("CLOB recorder process starttime is invalid") from exc
    cmdline = (process_root / "cmdline").read_bytes()
    if starttime_ticks <= 0 or not cmdline:
        raise ValueError("CLOB recorder process identity is invalid")
    return starttime_ticks, cmdline


def _open_file_descriptors(
    process_root: Path, *, file_dev: int, file_inode: int
) -> list[int]:
    matches: list[int] = []
    for descriptor in (process_root / "fd").iterdir():
        if not descriptor.name.isdigit():
            continue
        try:
            stat = descriptor.stat()
        except (FileNotFoundError, PermissionError):
            continue
        if stat.st_dev == file_dev and stat.st_ino == file_inode:
            matches.append(int(descriptor.name))
    return sorted(matches)


def record_active_clob_witness(
    clob_root: str | Path,
    output_root: str | Path,
    hour: str,
    *,
    service_name: str = DEFAULT_CLOB_SERVICE,
    proc_root: str | Path = DEFAULT_PROC_ROOT,
    recorder_identity_sampler: Callable[[Path, bytes, str], Mapping[str, Any]] = (
        _external_process_identity
    ),
) -> Path:
    hour_start_ns, hour_end_ns = _hour_bounds_ns(hour)
    source = Path(clob_root) / f"poly_clob.{hour}.jsonl.gz"
    path = _clock_path(output_root, hour)
    existing = _existing_clock_row(path, hour, "active_witness")
    immutable = {
        "schema": CLOB_CLOCK_SCHEMA,
        "evidence_kind": "active_witness",
        "target_hour": hour,
        "clob_file": source.name,
        "service": service_name,
    }
    if existing is not None:
        if any(existing.get(key) != value for key, value in immutable.items()):
            raise ValueError("CLOB clock sidecar drift")
        return path

    first_pid = _service_main_pid(service_name)
    process = Path(proc_root) / str(first_pid)
    first_starttime, first_cmdline = _process_identity(process)
    source_stat = source.stat()
    descriptors = _open_file_descriptors(
        process,
        file_dev=source_stat.st_dev,
        file_inode=source_stat.st_ino,
    )
    if not descriptors:
        raise ValueError("CLOB recorder has no fd open for target hour")
    provenance = capture_host_provenance()
    recorder_identity = recorder_identity_sampler(
        process,
        first_cmdline,
        service_name,
    )
    second_starttime, second_cmdline = _process_identity(process)
    second_pid = _service_main_pid(service_name)
    final_source_stat = source.stat()
    final_descriptors = _open_file_descriptors(
        process,
        file_dev=final_source_stat.st_dev,
        file_inode=final_source_stat.st_ino,
    )
    stable_descriptors = sorted(set(descriptors) & set(final_descriptors))
    if (
        first_pid != second_pid
        or first_starttime != second_starttime
        or first_cmdline != second_cmdline
        or not stable_descriptors
        or (source_stat.st_dev, source_stat.st_ino)
        != (final_source_stat.st_dev, final_source_stat.st_ino)
    ):
        raise ValueError("CLOB recorder changed during active witness")
    sampled_ns = int(provenance["provenance_sampled_ns"])
    offset_ns = int(provenance["realtime_minus_monotonic_ns"])
    clock_ticks = int(os.sysconf("SC_CLK_TCK"))
    monotonic_ns = sampled_ns - offset_ns
    if (
        not hour_start_ns <= sampled_ns < hour_end_ns
        or clock_ticks <= 0
        or monotonic_ns <= 0
        or first_starttime * 1_000_000_000 > monotonic_ns * clock_ticks
    ):
        raise ValueError("CLOB active witness clock is invalid")
    row = {
        **immutable,
        "main_pid": first_pid,
        "process_starttime_ticks": first_starttime,
        "clock_ticks_per_second": clock_ticks,
        "process_cmdline_sha256": hashlib.sha256(first_cmdline).hexdigest(),
        "active_file_dev": final_source_stat.st_dev,
        "active_file_inode": final_source_stat.st_ino,
        "open_fd": stable_descriptors[0],
        **recorder_identity,
        **provenance,
    }
    _append_clock_row(path, row)
    return path


def record_closed_clob_identity(
    clob_root: str | Path,
    output_root: str | Path,
    hour: str,
) -> Path:
    _, hour_end_ns = _hour_bounds_ns(hour)
    source = Path(clob_root) / f"poly_clob.{hour}.jsonl.gz"
    size, digest = _stable_file_identity(source)
    path = _clock_path(output_root, hour)
    immutable = {
        "schema": CLOB_CLOCK_SCHEMA,
        "evidence_kind": "closed_identity",
        "target_hour": hour,
        "clob_file": source.name,
        "bytes": size,
        "sha256": digest,
    }
    existing = _existing_clock_row(path, hour, "closed_identity")
    if existing is not None:
        if any(existing.get(key) != value for key, value in immutable.items()):
            raise ValueError("CLOB clock sidecar drift")
        return path
    provenance = capture_host_provenance()
    sampled_ns = int(provenance["provenance_sampled_ns"])
    if not (
        hour_end_ns <= sampled_ns <= hour_end_ns + MAXIMUM_CLOSE_AGE_S * 1_000_000_000
    ):
        raise ValueError("CLOB closed identity sampling lag is invalid")
    _append_clock_row(path, {**immutable, **provenance})
    return path


def record_current_active_clob_hour(
    clob_root: str | Path,
    output_root: str | Path,
    *,
    service_name: str = DEFAULT_CLOB_SERVICE,
    proc_root: str | Path = DEFAULT_PROC_ROOT,
    now_ns: int | None = None,
) -> Path:
    timestamp_ns = time.time_ns() if now_ns is None else int(now_ns)
    hour = time.strftime("%Y%m%dT%H", time.gmtime(timestamp_ns // 1_000_000_000))
    return record_active_clob_witness(
        clob_root,
        output_root,
        hour,
        service_name=service_name,
        proc_root=proc_root,
    )


def record_previous_closed_clob_hour(
    clob_root: str | Path,
    output_root: str | Path,
    *,
    now_ns: int | None = None,
    minimum_close_age_s: int = 300,
) -> Path | None:
    timestamp_ns = time.time_ns() if now_ns is None else int(now_ns)
    timestamp_s = timestamp_ns // 1_000_000_000
    current_hour_s = timestamp_s // 3_600 * 3_600
    if timestamp_s - current_hour_s < minimum_close_age_s:
        return None
    previous_hour = time.strftime("%Y%m%dT%H", time.gmtime(current_hour_s - 3_600))
    return record_closed_clob_identity(clob_root, output_root, previous_hour)


def append_row(root: str | Path, row: Mapping[str, Any]) -> Path:
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y%m%d", time.gmtime(int(row["recorded_ns"]) / 1e9))
    path = destination / f"settlement_meta.{day}.jsonl"
    line = (
        json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, line.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def run(
    root: str | Path,
    clob_root: str | Path,
    interval_s: float = 5.0,
    *,
    clob_service: str = DEFAULT_CLOB_SERVICE,
) -> None:
    seen: set[str] = set()
    witnessed_active_hours: set[str] = set()
    recorded_closed_hours: set[str] = set()
    while True:
        now = time.time()
        current_hour_s = int(now) // 3_600 * 3_600
        current_hour = time.strftime("%Y%m%dT%H", time.gmtime(current_hour_s))
        if current_hour not in witnessed_active_hours:
            try:
                record_current_active_clob_hour(
                    clob_root, root, service_name=clob_service
                )
            except (
                FileNotFoundError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
            ) as exc:
                print(f"{type(exc).__name__}: {exc}", flush=True)
            else:
                witnessed_active_hours.add(current_hour)
        previous_hour = time.strftime("%Y%m%dT%H", time.gmtime(current_hour_s - 3_600))
        if now - current_hour_s >= 300 and previous_hour not in recorded_closed_hours:
            try:
                record_previous_closed_clob_hour(clob_root, root)
            except (
                FileNotFoundError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
            ) as exc:
                print(f"{type(exc).__name__}: {exc}", flush=True)
            else:
                recorded_closed_hours.add(previous_hour)
        active = set(market_slugs(now))
        seen.intersection_update(active)
        for slug in sorted(active - seen):
            try:
                append_row(root, fetch_market(slug))
            except (
                KeyError,
                OSError,
                subprocess.SubprocessError,
                TimeoutError,
                TypeError,
                ValueError,
            ) as exc:
                print(f"{type(exc).__name__}: {exc}", flush=True)
            else:
                seen.add(slug)
        time.sleep(interval_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--clob-root", default=DEFAULT_CLOB_ROOT)
    parser.add_argument("--clob-service", default=DEFAULT_CLOB_SERVICE)
    args = parser.parse_args()
    run(
        args.output,
        args.clob_root,
        args.interval,
        clob_service=args.clob_service,
    )


if __name__ == "__main__":
    main()
