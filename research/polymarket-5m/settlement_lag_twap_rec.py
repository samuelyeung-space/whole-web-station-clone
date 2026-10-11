"""Record Polymarket's public final-TWAP feed in closed hourly clock epochs."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import signal
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import raw_rec
import settlement_lag_meta_rec as metadata
import settlement_lag_runtime as runtime

TWAP_SERVICE = "settlement-lag-twap-recorder.service"
TWAP_UNIT_PATH = "/etc/systemd/system/settlement-lag-twap-recorder.service"


def capture_current_recorder_identity() -> dict[str, Any]:
    return runtime.current_process_identity(
        service=TWAP_SERVICE,
        unit_path=TWAP_UNIT_PATH,
        script_path=Path(__file__),
    )


class HourlyLifecycleSink:
    """Raw-recorder compatible sink with explicit connection state per hour."""

    def __init__(
        self,
        source: str,
        output: str | Path,
        *,
        provenance_sampler: Callable[[], Mapping[str, Any]] = (
            metadata.capture_host_provenance
        ),
        identity_sampler: Callable[[], Mapping[str, Any]] = (
            capture_current_recorder_identity
        ),
    ) -> None:
        self.source = source
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.hour: str | None = None
        self.stream: Any | None = None
        self.last_flush = 0.0
        self.active_epoch: int | None = None
        self.active_details: dict[str, Any] = {}
        self.provenance_sampler = provenance_sampler
        self.identity_sampler = identity_sampler

    @staticmethod
    def _hour(ns: int) -> str:
        return time.strftime("%Y%m%dT%H", time.gmtime(ns / 1e9))

    @staticmethod
    def _hour_start_ns(hour: str) -> int:
        parsed = time.strptime(hour, "%Y%m%dT%H")
        return int(calendar_timegm(parsed) * 1e9)

    def _line(self, ns: int, text: str) -> None:
        if self.stream is None:
            raise RuntimeError("TWAP recorder output is not open")
        self.stream.write(f"{int(ns)}\t{text}\n")

    def _marker(
        self,
        ns: int,
        kind: str,
        epoch: int,
        *,
        segment_boundary: bool,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        payload = {
            "_recorder": {
                "schema": "recorder-lifecycle-v1",
                "kind": kind,
                "epoch": int(epoch),
                **dict(details or {}),
            }
        }
        if segment_boundary:
            payload["_recorder"]["segment_boundary"] = True
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _fresh_details(self, details: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **dict(details),
            **dict(self.provenance_sampler()),
            **dict(self.identity_sampler()),
        }

    def _rotate(self, ns: int) -> None:
        hour = self._hour(ns)
        if hour == self.hour:
            return
        if self.stream is not None:
            if self.active_epoch is not None and self.hour is not None:
                boundary_ns = self._hour_start_ns(hour) - 1
                self._line(
                    boundary_ns,
                    self._marker(
                        boundary_ns,
                        "disconnect",
                        self.active_epoch,
                        segment_boundary=True,
                        details=self._fresh_details(self.active_details),
                    ),
                )
            self.stream.close()
        self.hour = hour
        self.stream = gzip.open(  # noqa: SIM115 - the sink owns this rotating stream
            self.output / f"{self.source}.{hour}.txt.gz",
            "at",
            encoding="utf-8",
            compresslevel=3,
        )
        if self.active_epoch is not None:
            self._line(
                ns,
                self._marker(
                    ns,
                    "connection",
                    self.active_epoch,
                    segment_boundary=True,
                    details=self._fresh_details(self.active_details),
                ),
            )

    def write(self, ns: int, text: str) -> None:
        self._rotate(int(ns))
        self._line(int(ns), text.replace("\r", " ").replace("\n", " "))
        now = time.monotonic()
        if now - self.last_flush > 5 and self.stream is not None:
            self.stream.flush()
            self.last_flush = now

    def lifecycle(self, ns: int, kind: str, epoch: int, **details: Any) -> None:
        if kind not in {"connection", "disconnect"}:
            raise ValueError(f"unknown recorder lifecycle kind: {kind}")
        if kind == "connection":
            if self.active_epoch is not None:
                raise ValueError("TWAP recorder connected twice without disconnect")
            connection_details = dict(details)
            self.write(
                ns,
                self._marker(
                    ns,
                    kind,
                    epoch,
                    segment_boundary=False,
                    details=self._fresh_details(connection_details),
                ),
            )
            self.active_epoch = int(epoch)
            self.active_details = connection_details
            return
        if self.active_epoch != int(epoch):
            raise ValueError("TWAP recorder disconnect epoch drift")
        self.write(
            ns,
            self._marker(
                ns,
                kind,
                epoch,
                segment_boundary=False,
                details=self._fresh_details({**self.active_details, **details}),
            ),
        )
        self.active_epoch = None
        self.active_details = {}

    def close(self, *, now_ns: int | None = None) -> None:
        if self.stream is None:
            return
        if self.active_epoch is not None:
            timestamp = int(time.time_ns() if now_ns is None else now_ns)
            self._rotate(timestamp)
            self._line(
                timestamp,
                self._marker(
                    timestamp,
                    "disconnect",
                    self.active_epoch,
                    segment_boundary=True,
                    details=self._fresh_details(self.active_details),
                ),
            )
            self.active_epoch = None
            self.active_details = {}
        self.stream.close()
        self.stream = None


def calendar_timegm(value: time.struct_time) -> int:
    """Expose the UTC conversion as a small testable seam."""
    import calendar

    return calendar.timegm(value)


async def record(output: str | Path, hours: float) -> None:
    end = time.time() + hours * 3_600.0
    raw_rec.END = end
    counts = {"rtds_cl": 0}
    errors = {"rtds_cl": 0}
    state = {"fails": 0}
    sink = HourlyLifecycleSink(
        "settlement_twap",
        output,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        while time.time() < end and not stop.is_set():
            try:
                await raw_rec.ws_session(
                    "rtds_cl",
                    raw_rec.NEW["rtds_cl"],
                    sink,
                    counts,
                    state,
                )
            except (OSError, TimeoutError) as exc:
                if time.time() >= end or stop.is_set():
                    break
                errors["rtds_cl"] += 1
                state["fails"] += 1
                print(f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
                try:
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=min(30.0, 0.5 * 2 ** min(state["fails"], 6)),
                    )
                except TimeoutError:
                    pass
    finally:
        sink.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--hours", type=float, default=24 * 365 * 10)
    args = parser.parse_args()
    asyncio.run(record(args.output, args.hours))


if __name__ == "__main__":
    main()
