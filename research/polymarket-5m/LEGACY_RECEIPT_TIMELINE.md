# Legacy receipt timeline

Offline research intake, not a new strategy or execution claim. Existing frozen
October 10/11 protocols and rules are unchanged. No orders or network access.

## Reproduce

After running `legacy_recording_cache.py` once:

```bash
python3 legacy_receipt_timeline.py --input-cache LEGACY_CACHE_DIRECTORY --output-root receipt-index
python3 benchmark_legacy_receipt_timeline.py --input-cache LEGACY_CACHE_DIRECTORY --output-root receipt-index --report engineering.json
python3 -m unittest test_legacy_receipt_timeline -v
```

The result is a content-addressed SQLite index. It retains every input envelope,
including its source member and original row number. Input and output hashes are
verified. Only completed, integrity-checked builds are published. Failed builds
remain under `pending-*`, not as valid cache entries.

`Timeline.batches(start_ms, end_ms, kinds)` returns a chronological stream of
unordered equal-receipt batches. Within a batch, file order is provenance only;
it must not be treated as a transport sequence or used for intra-batch trading.
`Timeline.asof(kind, entity, decision_ms, max_age_ms)` uses strictly earlier local
receipts, not nearest prices. Conflicting latest records at one timestamp return
`ambiguous_equal_receipt`. Stale or absent inputs return no event. Unclocked rows
remain available for offline evidence inspection, not timeline/as-of decisions.

## Actual engineering audit: October 1 recording

Source: GitHub run 36807987110, artifact 11149495242. Archive SHA256:
`df26545b10da2401b7eb9ecf9efdbbac78c83f637336e982d0c140ee84b22701`.
Receipt bounds 2026-10-01 02:54:33.650–07:24:33.155 UTC. Bounds do not establish
continuous healthy coverage or complete markets.

* 2,113,707 rows retained; 2,113,539 clocked, 168 unclocked.
* Unclocked: 56 market metadata, 56 registry, 55 outcome, 1 other evidence row.
* 57,452 token snapshots: 10,785 file-order regressions, **zero within-token
  regressions**. Thus the earlier aggregate regression count must not be
  interpreted as evidence of individual token book corruption.
* First index build: 23.6314 seconds, separate from raw ingestion/decompression.
* Hash-verified index reuse: 1.0818 seconds.
* 1,000 fixed-stride as-of queries: 0.0847 seconds; all match an independently
  sorted raw snapshot reference, including tie handling.
* Streaming 57,452 snapshots plus 107,264 BTC prices: 2.2100 seconds,
  79,184 receipt batches. No strategy, FAK or cash ledger was run in this benchmark.
* Eight synthetic regression tests passed; they are not eight backtests.

## Remaining boundary

Sorting makes the records reusable; it does not establish connection continuity,
tick/minimum-size/fee availability, official label availability, or spendable
settlement cash timing. These must be admitted explicitly into an execution
adapter. The historical terminal labels may later score a development diagnostic
but must never be exposed as decision inputs or assumed cash release at expiry.
No daily market-coverage denominator, full seven-day replay or new PnL is claimed.
Formal OOS result and PnL remain null; qualified mechanisms remain 0/10.

Next work: map clocked records into the existing strategy endpoint with explicit
field-admission diagnostics, then batch-import the already-indexed Oct1–8 raw
artifacts. Preserve unknown metadata and separate any assumption-driven capacity
upper bounds from strict replay. Do not refit using frozen holdouts.
