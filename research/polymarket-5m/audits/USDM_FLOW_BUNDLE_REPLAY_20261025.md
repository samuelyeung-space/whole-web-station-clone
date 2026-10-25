# File-bound USD-M development replay

2026-10-25. New executable input entry point; no historical financial replay.

The previous bridge accepted in-memory tape/binding objects. `usdm_flow_bundle_replay.py` now accepts a pinned manifest and four separately hashed files, constructs the same recorder tape, and invokes the unchanged development bridge and existing partial-FAK/cash engine. No new signal, parameter, matching engine, or independent mechanism was introduced.

## Reproduce

From research/polymarket-5m with the existing dependencies present:

```bash
python3 usdm_flow_bundle_replay.py /consumed-development/bundle/manifest.json \
  --sha256 ACTUAL_MANIFEST_SHA256 --output /scratch/development_result.json
python3 -m unittest test_usdm_flow_bundle_replay -v
```

The manifest must use schema `usdm-flow-bundle-replay-dev-v1`, split `development`, input_evidence_class `consumed_development` (or `synthetic_fixture`), nonempty consumption_provenance, capture_run_id, capture_clock_id `<capture_run_id>:recv_ms`, the existing pinned recorder_contract `paper-trader-7bf9cf2a-usdm-raw-trade-v1`, time_unit `MILLISECOND`, and trades_encoding `jsonl` or `gzip-jsonl`.

Its `files` object must contain exactly `events`, `trades`, `coverage`, `bindings`. Each descriptor requires path, bytes, sha256. All paths must resolve to distinct files inside the manifest directory. Every file is read once into a bounded byte snapshot, and all four hashes/sizes are checked before parsing any input rows. Compressed inputs have a separate decompressed size bound. A mismatch raises before any native parse or replay. Duplicate JSON keys and nonfinite JSON tokens are rejected.

- events: existing normalized JSONL, starting with the development dataset header and capture_clock_id matching the native tape. Existing market constraints, fee evidence, receipt-clock books, decisions and official outcomes remain required by the engine.
- trades: original strict recorder JSONL, optionally gzip. Every BTCUSDT trade goes through the existing pinned adapter; any invalid quantity/price, conflicting ID or epoch failure aborts the run. No corrupt rows are deleted or repaired. Other row kinds remain in the original input snapshot and are counted separately. At least one BTC trade is required.
- coverage: JSON array of existing CoverageInterval objects: exchange, instrument, capture_run_id, start_ms, end_ms, known_at_ms, status, provenance. Each must match binance_usdm/BTCUSDT/the exact capture run. Coverage is never inferred from prints, IDs, archive size or timestamps. Empty coverage is accepted as an explicit blocker and produces no order attempts.
- bindings: JSON array with exactly market_id, decision_recv_ms, capture_clock_id, tau_evidence per row. Tau evidence retains the existing value/as_of_ms/available_ms/capture_clock_id/provenance contract. The tape object is created internally, so serialized bindings cannot substitute an arbitrary tape.

Output adds manifest/input SHA256 identities, raw native/other counts, supplied coverage count, and the hashes of eight loaded module files. The imported Python process is **not sealed**; those module file hashes do not prove immutable code execution. Input consumption, matching clocks and truthful coverage remain caller attestations, requiring independent review before a real research claim. No OOS evidence class is accepted, and no old freeze is inherited.

## New checks and evidence limits

Seven new synthetic file-boundary checks passed: valid bundle reaches existing engine; tampered tape is rejected before parsing; mismatched clock and coverage run are rejected; absent coverage produces no attempts; corrupt native quantity aborts without replay; manifest hash/split/duplicate-key/path escapes are rejected; invalid CLI manifest produces no output. These test new serialized input admission. The six earlier bridge checks and prior grids were not repeated as new progress. Eight existing dependency hashes still match the prior recorded versions.

No real bundle was supplied or executed. The known consumed 400040 tape remains corrupt and lacks independently justified coverage; this runner does not fix either issue. Synthetic result cells are not historical returns. New financial replays: 0. New candidates: 0. New independent mechanisms: 0. New full UTC days admitted: 0. Rank7 PnL: null. Formal OOS: null. Qualified mechanisms: 0/10.

Source head remains 2c13d5e71f3b879f7d7f8ee291dc5cf3e165e8c5; research parent d9500e7b768542eb218f63051c9c23092bb127f1. Raw402038 transport is already preserved; its six source/processed artifact identities are unchanged. No repeated raw download, semantic market/financial reading, full workflow log fetch, AWS access, orders, production changes or workflow dispatch. Reserved Oct24–Oct31 diagnostic content and prior exposure record stay unchanged. Old Oct10/11 freezes and Oct12–19 protocol stay unchanged.

Next: bind legitimate already-consumed development files with source identities and independently justified receipt coverage; only then perform a real development replay. Candidate/execution registration and a strictly later untouched confirmation window must be handled separately. Rank7 still requires seven consecutive complete UTC days, exactly 2016 aligned BTC5m slots, independent $1000 cash per strategy/scenario, target 100 shares, and all fixed latency/depth/cost alternatives. Missing results remain null.
