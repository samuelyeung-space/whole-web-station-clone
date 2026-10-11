"""Lossless external HF trade receipts and explicitly covered 15/60s features.

Offline only: no downloads, fitting, trading, PnL or new candidate templates.
The existing mine500 catalog consumes ``flow_15s`` and ``flow_60s`` in QUOTE
volume units, so each print weighs price * size. Neither price ticks nor an
unknown taker-side value can manufacture an aggressor direction.

HF's stored ``recv_ts_ms`` is the archive recorder's receipt clock, not evidence
about our current execution environment. Complete coverage is NOT inferred
from print frequency, a run id, timestamps, or a whole-day quality flag. The
caller must supply separately justified, receipt-causal coverage intervals.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
import math
from typing import Mapping


VERSION = "historical-external-flow-receipts-v1"
WINDOWS_SECONDS = (15, 60)


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " must be a numeric scalar")
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError("invalid " + name)
    return value


def _identifier(value, name):
    # Never round a large float into a trade ID: HF describes IDs as strings.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(name + " must be an exact string/integer identity")
    result = str(value).strip()
    if not result:
        raise ValueError("missing " + name)
    return result


@dataclass(frozen=True)
class TradeReceipt:
    exchange: str
    instrument: str
    trade_id: str
    capture_run_id: str
    source_event_ms: int | float
    local_recv_ms: int | float
    price: int | float
    quantity: int | float
    raw_taker_side: object
    aggressor_side: str | None
    side_status: str

    @property
    def identity(self):
        return self.exchange, self.instrument, self.trade_id

    @property
    def economic_identity(self):
        return self.source_event_ms, self.price, self.quantity, self.aggressor_side

    @property
    def quote_volume(self):
        return self.price * self.quantity

    def to_dict(self):
        return asdict(self)


def normalize_hf_trade(row: Mapping) -> TradeReceipt:
    """Preserve real ID, both clocks, quantity, and the raw aggressor field.

    Input names are the saved HF ``trades`` schema. Timestamp units are already
    milliseconds and are never guessed or converted from source-clock order.
    Unknown side stays unknown; invalid price/quantity/identity fails closed.
    The caller explicitly routes the desired exchange and instrument.
    """
    exchange = _identifier(row.get("exchange"), "exchange").lower()
    instrument = _identifier(row.get("instrument"), "instrument").upper()
    trade_id = _identifier(row.get("trade_id"), "trade_id")
    run_id = _identifier(row.get("_run_id"), "_run_id")
    source = _number(row.get("trade_ts_ms"), "trade_ts_ms")
    recv = _number(row.get("recv_ts_ms"), "recv_ts_ms")
    price = _number(row.get("price"), "price", positive=True)
    quantity = _number(row.get("size"), "size", positive=True)
    if not math.isfinite(price * quantity):
        raise ValueError("quote volume overflow")
    raw = row.get("taker_side")
    text = raw.strip().lower() if isinstance(raw, str) else None
    side = text.upper() if text in ("buy", "sell") else None
    status = "explicit_hf_taker_side" if side else "missing_taker_side" if raw is None else "unknown_taker_side"
    return TradeReceipt(exchange, instrument, trade_id, run_id, source, recv,
                        price, quantity, raw, side, status)


@dataclass(frozen=True)
class CoverageInterval:
    """Coverage over (start_ms, end_ms], known no earlier than known_at_ms.

    Status is ``complete``, ``gap``, or ``unhealthy``. Evidence must describe an
    already observed interval, and its provenance is retained in every feature
    audit. Availability checks prevent later diagnostics from screening past
    decisions. This record is an attestation supplied by the caller; the class
    cannot verify that an external source's claim is truthful.
    """
    exchange: str
    instrument: str
    capture_run_id: str
    start_ms: int | float
    end_ms: int | float
    known_at_ms: int | float
    status: str
    provenance: str

    def __post_init__(self):
        for name in ("start_ms", "end_ms", "known_at_ms"):
            _number(getattr(self, name), name)
        if self.end_ms <= self.start_ms:
            raise ValueError("coverage interval must have positive duration")
        if self.known_at_ms < self.end_ms:
            raise ValueError("coverage cannot certify a not-yet-observed interval")
        if self.status not in {"complete", "gap", "unhealthy"}:
            raise ValueError("unknown coverage status")
        for name in ("exchange", "instrument", "capture_run_id", "provenance"):
            _identifier(getattr(self, name), name)


class ReceiptFlowTape:
    """Append receipt-ordered trades, retain duplicates, and compute old fields.

    Distinct trade IDs always remain distinct, even if every numerical field
    is equal. Identical repeat receipts of one exchange/instrument/trade ID are
    retained in ``receipts`` for audit; only its first receipt contributes to
    flow. A conflicting repeat ID fails closed rather than rewriting history.
    Unknown directions on the first receipt are not repaired from later data.
    """
    def __init__(self, *, exchange="binance", instrument="BTCUSDT"):
        self.exchange = _identifier(exchange, "exchange").lower()
        self.instrument = _identifier(instrument, "instrument").upper()
        self.receipts: list[TradeReceipt] = []
        self.first_receipts: list[TradeReceipt] = []
        self.coverage: list[CoverageInterval] = []
        self._first_by_id = {}
        self._first_times = []
        self._last_recv = -math.inf
        self.duplicate_receipts = 0

    def add_trade(self, row: Mapping) -> TradeReceipt:
        trade = normalize_hf_trade(row)
        if (trade.exchange, trade.instrument) != (self.exchange, self.instrument):
            raise ValueError("explicit source/instrument routing is required")
        if trade.local_recv_ms < self._last_recv:
            raise ValueError("receipt clock moved backwards; source-time sorting is forbidden")
        previous = self._first_by_id.get(trade.identity)
        if previous is not None and previous.economic_identity != trade.economic_identity:
            raise ValueError("conflicting repeated trade ID: " + trade.trade_id)
        self.receipts.append(trade)
        self._last_recv = trade.local_recv_ms
        if previous is None:
            self._first_by_id[trade.identity] = trade
            self.first_receipts.append(trade)
            self._first_times.append(trade.local_recv_ms)
        else:
            self.duplicate_receipts += 1
        return trade

    def add_coverage(self, interval: CoverageInterval):
        if (interval.exchange.lower(), interval.instrument.upper()) != (self.exchange, self.instrument):
            raise ValueError("coverage refers to a different source or instrument")
        self.coverage.append(interval)

    def _window(self, decision, seconds):
        lo = decision - seconds * 1000
        left, right = bisect_right(self._first_times, lo), bisect_right(self._first_times, decision)
        trades = self.first_receipts[left:right]
        observable = [c for c in self.coverage if c.known_at_ms <= decision and
                      c.end_ms > lo and c.start_ms < decision]
        audit = dict(window_seconds=seconds, lower_exclusive_ms=lo,
                     upper_inclusive_ms=decision, unique_trade_count=len(trades),
                     unknown_side_count=sum(t.aggressor_side is None for t in trades),
                     capture_run_ids=sorted({t.capture_run_id for t in trades}),
                     newest_input_recv_ms=max((t.local_recv_ms for t in trades), default=None),
                     quote_volume=math.fsum(t.quote_volume for t in trades),
                     coverage_records=[asdict(c) for c in observable],
                     coverage_truth_independently_verified=False)
        def unavailable(reason):
            return dict(value=None, available_ms=None, status=reason, **audit)
        if len(audit["capture_run_ids"]) > 1:
            return unavailable("unknown_cross_capture_epoch")
        # A source run must span the entire trailing window with caller-provided
        # coverage. Trade timestamps themselves never establish continuity.
        run = audit["capture_run_ids"][0] if trades else None
        candidates = [c for c in observable if run is None or c.capture_run_id == run]
        if any(c.status != "complete" for c in candidates):
            return unavailable("unknown_explicit_gap_or_unhealthy")
        complete = [c for c in candidates if c.status == "complete"]
        epochs = {c.capture_run_id for c in complete}
        if run is None and len(epochs) != 1:
            return unavailable("unknown_missing_or_ambiguous_coverage")
        cursor = lo
        for c in sorted(complete, key=lambda x: (x.start_ms, x.end_ms)):
            if c.start_ms > cursor:
                break
            cursor = max(cursor, c.end_ms)
        audit["covered_prefix_ms"] = max(0., min(cursor, decision) - lo)
        if cursor < decision:
            return unavailable("unknown_incomplete_coverage")
        if not trades or audit["quote_volume"] <= 0:
            return unavailable("unknown_no_positive_trade_volume")
        if audit["unknown_side_count"]:
            return unavailable("unknown_aggressor_side")
        signed = math.fsum(t.quote_volume if t.aggressor_side == "BUY" else -t.quote_volume for t in trades)
        available = max(audit["newest_input_recv_ms"], max(c.known_at_ms for c in complete))
        return dict(value=signed / audit["quote_volume"], available_ms=available,
                    status="available_observed_capture_with_explicit_coverage", **audit)

    def features(self, decision_recv_ms):
        """Only received trades and already available coverage affect a feature."""
        decision = _number(decision_recv_ms, "decision_recv_ms")
        windows = {f"flow_{seconds}s": self._window(decision, seconds) for seconds in WINDOWS_SECONDS}
        return dict(version=VERSION, decision_recv_ms=decision,
                    exchange=self.exchange, instrument=self.instrument,
                    features={key: row["value"] for key, row in windows.items()},
                    feature_available_ms={key: row["available_ms"] for key, row in windows.items()},
                    audit=windows,
                    volume_units="quote_volume=price*size",
                    capture_clock="HF archive recorder recv_ts_ms; not current user's execution latency",
                    template_logic="reuse existing mine500_catalog; no new templates or thresholds",
                    actual_backtests_performed=0, formal_oos_result=None)

    def input_audit(self):
        return dict(version=VERSION, retained_receipts=len(self.receipts),
                    unique_economic_trades=len(self.first_receipts),
                    repeated_receipts_retained=self.duplicate_receipts,
                    unknown_first_receipt_sides=sum(t.aggressor_side is None for t in self.first_receipts),
                    coverage_intervals=len(self.coverage),
                    dedup_identity="exchange,instrument,trade_id; never price/size/timestamps alone",
                    both_clocks_preserved=True, raw_taker_side_preserved=True,
                    completeness_inferred_from_trade_frequency=False,
                    actual_backtests_performed=0)
