# Exact aggregate-range index, development parser v2

2026-10-20. Offline research only. No capture deployment, orders, fitting,
historical strategy backtests, new candidates or PnL. The existing strategy
catalog, first-receipt clocks, quantity/side rules and execution code are not
changed. Frozen experiments cannot silently adopt this changed parser hash.

## Gap addressed

The v1 native `aggTrade` bridge checked a distinct aggregate's inclusive
constituent raw-trade ID range against every prior aggregate. For n disjoint
ordered aggregates this performs n(n-1)/2 range checks. At 100,000 aggregates
that is 4,999,950,000 checks, an algorithmic count, not measured replay time.

V2 keeps sorted disjoint exact-integer starts/ends. Ordered disjoint arrivals
append in constant time; out-of-order admission checks the two neighboring
ranges after binary search. Lookup uses logarithmic probes, but out-of-order
list insertion remains linear and adversarial reverse insertion can still be
quadratic overall. This is not a bounded-memory or whole-replay speed claim:
native receipts, signatures and the existing audit dictionary remain retained.

Inclusive endpoints matter: [10,12] and [12,14] conflict, while [10,12] and
[13,15] do not. Huge IDs remain Python integers, never floats. Identical
duplicate receipts remain in the audit but do not insert a second interval
or count volume twice. Source-ID reordering is allowed only in already valid
local-receipt order; source time is never used to reorder data.

The index commits only after the established receipt engine accepts the row.
An overlap/conflicting ID or invalid receipt clock still fails the tape closed.
A future error cannot retroactively filter an earlier feature. An indexed
range is never evidence of feed continuity, completeness or coverage.

## Verification

Run from `research/polymarket-5m`:

```sh
python3 -m unittest test_native_flow_aggregate_index test_native_flow_catalog_join test_binance_native_flow test_historical_flow_receipts -v
```

65 synthetic engineering tests pass: 15 new tests and 50 existing regressions.
New tests include 9,600 randomized interval proposals against a full-scan
oracle; exact large integers, singleton/neighbor/overlap ranges; source-ID
reorder; duplicate volume; failure atomicity; future-error invariance; and
1,000 shuffled aggregates plus repeats yielding the same receipts, feature
audits and values as the previous full-scan oracle. The 100,000-range probe
check verifies logarithmic lookup without a hardware-sensitive timing gate.
These counts are not candidate attempts, fills, data coverage or backtests.

Adoption requires a new rule/model/input/execution dependency closure and a
strictly later untouched confirmation interval if that closure changes.
Existing Oct10/11 and Oct12-19 frozen protocols remain unchanged. Missing
7-day input keeps ranking/PnL/OOS null; engineering passes never change that.
