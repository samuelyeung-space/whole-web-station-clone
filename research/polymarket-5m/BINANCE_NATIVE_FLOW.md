# Native Binance trade preservation bridge

Offline research only. This bridges native Binance spot payloads into the
existing `historical_flow_receipts.ReceiptFlowTape`; it does not add strategies,
change frozen schemas, subscribe to feeds, deploy recorders or place orders.
Source contract checked 2026-10-16:
https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams

## Required capture contract

Keep the entire native payload plus independently measured `local_recv_ms`,
explicit `capture_run_id` (recording epoch), and declared `time_unit`.

| Native field | Preserved meaning |
| --- | --- |
| e / s | Exact stream kind and symbol |
| t, or a plus f/l | Exact trade ID, or aggregate ID and constituent range |
| p / q | Price and quantity; original decimal strings retained |
| T / E | Source trade and event timestamps; not receipt timestamps |
| m | Buyer-is-maker boolean: true means SELL aggressor, false means BUY |
| M | Ignored field, never an alternative aggressor flag |

This version supports explicitly declared millisecond timestamps only. It
rejects unsupported units instead of guessing. Native boolean strings, float
IDs, routing mismatches, invalid quantity and ambiguous overlapping aggregate
ranges fail closed. Missing m remains unknown. No unit quantities, fabricated
IDs, tick-rule sides or inferred complete coverage are used.

One tape supports exactly one stream kind and symbol; trade and aggTrade may
represent the same economic executions and must never be summed. Identical
repeat receipts stay in the audit but only the first contributes to flow.
Economic identity checks use exact decimal values before float feature
conversion. Every native payload is retained alongside the engine row.

## Integration

```python
from binance_native_flow import NativeFlowTape
from historical_flow_receipts import CoverageInterval

tape = NativeFlowTape(stream_kind='trade', time_unit='MILLISECOND')
# For each saved packet, in local receipt order:
tape.add_trade(payload, local_recv_ms=recorded_recv_ms,
               capture_run_id=recording_epoch)
# Add separately justified, already observable coverage evidence. Never infer
# a complete interval merely from frequent prints, IDs or a whole-day verdict.
tape.add_coverage(coverage_interval)
features = tape.features(decision_recv_ms)
```

The result exposes the existing `flow_15s` and `flow_60s`, weighted by quote
volume (price times quantity). All inputs and coverage must be known by the
decision. A parse failure blocks subsequent decisions even if a caller catches
the exception; start a new recording epoch after correcting the source. It
cannot silently drop malformed prints and continue computing biased volume.

Run synthetic tests:

```
python -m unittest test_binance_native_flow test_historical_flow_receipts
```

The old four-field BINANCE_WS_TRADE price archive is not a native payload and
cannot be reconstructed by this bridge. It remains useful for price-only work,
but not measured aggressor-volume research. No historical flow backtest or OOS
result follows from parser tests. Existing blocked candidates stay blocked
until real native captures and causal coverage evidence are delivered.
