# Cached research replay

Offline only: no orders, network, deployment, or model fitting. This entrypoint reuses the existing execution engine; it does not replace it.

## Prepare once

```sh
python3 cached_strategy_replay.py prepare --endpoint endpoint.jsonl.gz --prices btc_trades_legacy_units.jsonl.gz --scores frozen_scores.jsonl.gz --cache cache
```

Inputs must be local-receipt-causal normalized events, trade-only price records (`receive_ts`, `trade_ts` in seconds), and separately frozen model scores. Never train on the evaluation interval. Cache keys include input content and relevant code hashes. Repeat preparation reuses an integrity-checked cache. A changed feature requirement needs a new feature cache, not fabricated fields.

## Insert strategies and replay

```sh
python3 cached_strategy_replay.py run --cache cache/CONTENT_HASH --strategy joint_probability_research:JPR-035 --strategy mean_regime_research:MAR-089 --strategy mean_regime_research:MAR-099 --shares 5 --out new_run
```

Strategy modules provide `catalog()` or `candidates()`, plus `signal(candidate, features)`. Candidate IDs must be unique. Required missing features are rejected. Default scenarios are 500/600/1000ms, fee-aware, fixed decision-time limits, partial FAK depth sensitivity and a separate $1000 cash ledger for each candidate/latency. Outputs are registration, all attempts, and execution audit. Alternatives must not be summed. Shared-cash arbitration remains in `joint_portfolio_research.py`.

## Measured acceptance check

Oct13's 4.002661-hour input: feature cache preparation 14.486 seconds; 3 rules x 3 latencies replay 7.369 seconds, including cache reading and result writing but excluding interpreter startup and first raw normalization. All 30 attempt records and economic summary fields matched the prior uncached replay exactly. Existing first-missing-feature diagnostic attribution depends on Python set ordering; invalid totals match. This is an engineering reproduction, not nine new alpha trials or seven-day performance evidence.

## Remaining work

Index and ingest all accessible recordings from Oct1, not only recent uploads. Raw recording acquisition, schema normalization and fixed-model scoring remain upstream steps and are not yet automatic. Complete historical coverage has not been established. The current entrypoint covers single-leg taker simulations, not validated maker queues or multi-leg hedges. Preserve old formal freezes; schema/model changes require a later untouched confirmation protocol.

## Legacy archive intake (separate from execution admission)

```sh
python3 legacy_recording_cache.py --input recording-A.zip recording-B.zip --cache legacy-cache --index legacy-index.json
python3 -m unittest test_legacy_recording_cache -v
```

This offline, batch-capable intake reads BTC members once, preserves every raw field, separates rows without local receipt clocks, and indexes each archive by content and code hashes. Repeated imports verify output hashes and reuse the cache. It does NOT fabricate aggressor side, volume, metadata availability, settlement cash availability, or source connection epochs. Cached legacy envelopes are not the endpoint tape expected by `cached_strategy_replay.py`: downstream causal schema admission remains necessary. In particular, do not pass post-resolution tick/fee values or Gamma close time off as locally known historical values.

Measured on run 36807987110 / artifact 11149495242: 12 streams, 2,113,707 rows, 56 market metadata records. Initial intake 43.843 seconds; repeat verified reuse 0.085 seconds. Counts and receipt-regression counts agree with independent `mine500_legacy_recording_audit.py`. BTC snapshot bounds are 2026-10-01 02:54:33.913 to 07:24:33.155 UTC (not continuous health). The 57,452 snapshot rows contain 10,785 local-receipt regressions in file order; 1,696,017 separate probability observations and 107,264 BTC spot trade rows are receipt-monotone. Never iterate the snapshot file as a chronological tape without explicit receipt ordering and equal-time tie treatment. No source clocks may be borrowed from a nearest-price match.

Six unit tests pass, covering missing-clock preservation, timestamp units, invalid clocks, raw preservation, idempotency and cache corruption rejection. This is ingestion verification, no new candidate evaluation and no PnL; strict readiness remains false.
