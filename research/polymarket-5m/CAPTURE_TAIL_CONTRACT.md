# Sealed recorder tail diagnostic

Research-only metadata check. No recorder deployment, execution changes, raw
price access, labels, fitting, orders or modification of old frozen protocols.

Run `capture_tail_contract.py --runtime capture-runtime.json --out NEW.json`.
Exit2 means a terminal requirement is blocked; the evidence JSON is retained.
The default explicitly requires `binance-strict`, `clob-btc` and `rtds` last
enqueue no more than30seconds before sealed end, plus a complete/drained writer.
This coarse age is NOT the execution engine's250ms quote freshness, and cannot
be used to certify uninterrupted recording, complete UTC dates or market slots.
It includes shutdown drain; prolonged absence of writes is not by itself proof
of a broken connection or its cause. Thresholds are engineering contracts, not
empirically calibrated market-specific cadence guarantees.

All strategies needing a different source contract must declare it explicitly.
Optional missing sources must not silently become available, and a passing
terminal diagnostic never admits replay or creates a zero-PnL result.
Validate continuous per-market coverage, execution schemas and official outcomes
separately under the existing common-seven-day protocol. Do not select trades
using health information that was not locally available at decision/match time.
