"""Receipt-causal trend context, not an execution or profitability model.

Fixed 60s bars, SMA10, Wilder ATR10 and Supertrend(10,3). Bars describe the
received trade stream, NOT venue/exchange OHLC. The first traded bucket after
every reset is discarded conservatively. No empty bar is forward filled.
Call ingest in receipt order, then snapshot at nondecreasing decision times.
Ingested future rows stay pending until their receipt is available.
"""
from collections import Counter, deque
from copy import deepcopy
import math

BAR_MS = 60_000
PERIOD = 10
MULTIPLIER = 3.0
MAX_TRADE_GAP_MS = 5_000
MAX_TRADE_AGE_MS = 1_000
# ctx_epoch is a numeric reset-generation identifier, NOT a source timestamp
# or proof of market/feed coverage. It changes after every context reset.
FEATURE_NAMES = ("ctx_ma_direction", "ctx_st_direction", "ctx_price", "ctx_r1_bps", "ctx_epoch")


def _number(x):
    return type(x) in (int, float) and math.isfinite(x)


class TrendContext:
    def __init__(self):
        self.pending = deque()
        self.counts = Counter()
        self.closed_bars = 0
        self.decision_ms = None
        self.last_receipt = None
        self.raw_epoch = None
        self.generation = 0
        self.connected = False
        self._clear()

    def _clear(self):
        self.bar = None
        self.discard_bucket = None
        self.last_trade_ms = None
        self.last_price = None
        self.prices = deque()
        self.closes = deque(maxlen=11)
        self.tr_seed = []
        self.atr = None
        self.prev_close = None
        self.upper = self.lower = self.supertrend = None
        self.st_direction = None
        self.sma = self.sma_prev = None

    def _reset(self, reason):
        self.generation += 1
        self.counts["reset_" + reason] += 1
        self._clear()

    def ingest(self, row):
        """Queue an immutable row; future-receipt state is not yet visible."""
        if not isinstance(row, dict) or not _number(row.get("recv_ms")):
            raise ValueError("finite recv_ms required")
        self.pending.append(deepcopy(row))
        self.counts["rows_ingested"] += 1

    def _close_bar(self):
        bar = self.bar
        self.bar = None
        if bar is None:
            return
        if bar["start"] == self.discard_bucket:
            self.counts["first_bucket_discarded"] += 1
            return
        h, l, c = bar["high"], bar["low"], bar["close"]
        tr = h - l if self.prev_close is None else max(h-l, abs(h-self.prev_close), abs(l-self.prev_close))
        self.closes.append(c)
        self.closed_bars += 1
        self.counts["closed_bars"] += 1
        if self.atr is None:
            self.tr_seed.append(tr)
            if len(self.tr_seed) == PERIOD:
                self.atr = sum(self.tr_seed) / PERIOD
        else:
            self.atr = ((PERIOD - 1) * self.atr + tr) / PERIOD
        self.sma_prev = self.sma
        if len(self.closes) >= PERIOD:
            self.sma = sum(list(self.closes)[-PERIOD:]) / PERIOD
        if self.atr is not None:
            basic_upper = (h+l)/2 + MULTIPLIER*self.atr
            basic_lower = (h+l)/2 - MULTIPLIER*self.atr
            if self.upper is None:
                upper, lower, direction = basic_upper, basic_lower, -1
            else:
                upper = basic_upper if basic_upper < self.upper or self.prev_close > self.upper else self.upper
                lower = basic_lower if basic_lower > self.lower or self.prev_close < self.lower else self.lower
                # TradingView's negative direction denotes up; this interface
                # deliberately returns +1 for up and -1 for down.
                if self.supertrend == self.upper:
                    direction = 1 if c > upper else -1
                else:
                    direction = -1 if c < lower else 1
            self.upper, self.lower = upper, lower
            self.st_direction = direction
            self.supertrend = lower if direction == 1 else upper
        self.prev_close = c

    def _consume(self, row):
        now, kind = float(row["recv_ms"]), row.get("kind")
        if self.last_receipt is not None and now < self.last_receipt:
            self._reset("receipt_regression")
        self.last_receipt = now
        epoch = row.get("connection_epoch")
        if epoch != self.raw_epoch:
            self._reset("epoch_transition")
            self.raw_epoch = epoch
        if kind == "spot_connection":
            self._reset("connection")
            self.connected = True
            return
        if kind in ("spot_disconnect", "spot_error"):
            self._reset("disconnect")
            self.connected = False
            return
        if kind != "spot_trade":
            return
        price = row.get("price")
        if not self.connected or not _number(price) or price <= 0:
            self.counts["invalid_or_disconnected_trade"] += 1
            return
        if self.last_trade_ms is not None and now-self.last_trade_ms > MAX_TRADE_GAP_MS:
            self._reset("trade_gap")
        bucket = int(now // BAR_MS) * BAR_MS
        if self.bar is not None and bucket != self.bar["start"]:
            if bucket != self.bar["start"] + BAR_MS:
                self._reset("empty_bar")
            else:
                self._close_bar()
        if self.discard_bucket is None:
            self.discard_bucket = bucket
        if self.bar is None:
            self.bar = dict(start=bucket, high=price, low=price, close=price)
        else:
            self.bar["high"] = max(self.bar["high"], price)
            self.bar["low"] = min(self.bar["low"], price)
            self.bar["close"] = price
        self.last_trade_ms, self.last_price = now, price
        self.prices.append((now, price))
        while len(self.prices) > 1 and self.prices[1][0] < now - 10_000:
            self.prices.popleft()
        self.counts["trades_consumed"] += 1

    def snapshot(self, decision_ms):
        if not _number(decision_ms):
            raise ValueError("finite decision time required")
        if self.decision_ms is not None and decision_ms < self.decision_ms:
            raise ValueError("decision time must not regress")
        self.decision_ms = float(decision_ms)
        while self.pending and self.pending[0]["recv_ms"] <= decision_ms:
            self._consume(self.pending.popleft())
        if self.bar is not None and self.bar["start"] + BAR_MS <= decision_ms:
            self._close_bar()
        empty = dict.fromkeys(FEATURE_NAMES)
        ready = (self.connected and self.last_trade_ms is not None
                 and 0 <= decision_ms-self.last_trade_ms <= MAX_TRADE_AGE_MS
                 and self.sma is not None and self.sma_prev is not None
                 and self.st_direction is not None)
        if not ready:
            self.counts["snapshots_unavailable"] += 1
            return empty
        slope = self.sma-self.sma_prev
        direction = (1 if self.last_price > self.sma and slope > 0 else
                     -1 if self.last_price < self.sma and slope < 0 else 0)
        anchor = next(((t,p) for t,p in reversed(self.prices) if t <= decision_ms-1000), None)
        r1 = (10000*math.log(self.last_price/anchor[1])
              if anchor and 0 <= decision_ms-1000-anchor[0] <= MAX_TRADE_AGE_MS else None)
        self.counts["snapshots_available"] += 1
        return dict(ctx_ma_direction=direction, ctx_st_direction=self.st_direction,
                    ctx_price=self.last_price, ctx_r1_bps=r1, ctx_epoch=self.generation)

    def audit(self):
        return {"parameters": {"bar_ms": BAR_MS, "sma_period": PERIOD,
                "atr_period": PERIOD, "supertrend_multiplier": MULTIPLIER,
                "max_trade_gap_ms": MAX_TRADE_GAP_MS, "max_trade_age_ms": MAX_TRADE_AGE_MS},
                "counters": dict(self.counts), "pending_rows": len(self.pending),
                "bar_basis": "receipt_time_trade_ohlc_not_exchange_ohlc",
                "missing_bars_forward_filled": False,
                "aggressor_side_inferred": False,
                "market_or_feed_completeness_claim": False}
