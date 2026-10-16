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
