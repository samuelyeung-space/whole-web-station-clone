# Legacy batch intake checkpoint — 2026-10-16

Research only. No orders, production changes, new strategy evaluations, or OOS claims.

## Completed

- Nine Oct1/2-created forward artifacts ingested: eight new, one previously ingested. Seven downloaded this pass; one reused from the existing package.
- 21,468,393 raw-envelope rows; 156.85 seconds batch ingestion. This measures ingestion, not backtest speed.
- Reusable three-worker resumable intake and three synthetic tests committed at `b6bd60e112b855365d46cc0cf43c56793e6a4305`.
- Exact run/artifact IDs, archive SHA256, cache keys, ranges and missing fields: `legacy_batch_coverage_20261016.json`, audit commit `a1ead7e9efe9e601d01307d17e188f0648d086a1`.

## Denominators

| UTC date | Theoretical five-minute slots | Slots with any book receipt | Slots without a book receipt in this batch |
|---|---:|---:|---:|
| 2026-10-01 | 288 | 220 | 68 |
| 2026-10-02 | 288 | 233 | 55 |
| 2026-10-03, tail only | 288 | 42 | 246 |

Slot presence is not continuous full-market coverage. Registered markets use market-start date; book slots use local receipt date, so these counts differ. Missing slots apply only to these nine archives, not all recordings. Raw row counts are not overlapping-archive deduplicated; slot and market counts are deduplicated. Signals, attempts, fills, healthy complete markets and PnL remain null because strategy replay has not run.

## Next executable work

1. Resume the existing historical forward inventory (37 runs / 35 artifacts): skip these nine using SHA256 and process remaining available artifacts. Do not redownload the large Oct9 archive without need.
2. Build/reuse causal local-receipt indices and enumerate daily coverage before selecting complete seven-day spans.
3. Finish explicit legacy-to-execution field admission, including per-token sides, fees, tick/minimum size, endpoint labels and their availability. Missing aggressor side/size must remain unknown, not inferred from tick moves. Queue claims require appropriate evidence.
4. Preserve first signal, fixed limit, partial/zero FAK, actual 500/600/1000 ms matching clock and shared $1000 cash accounting. Do not use end-of-market time as available settlement cash without evidence.
5. Replay only eligible mechanisms on already-consumed historical development data. Do not amend Oct10/11 freezes or label new revisions as original OOS. Record all failures and attempts.

## Persistence status

Code and compact audit are committed to the dedicated research branch. Replacement of the original three deliverables failed at upload preparation (`prepare_uploads failed`); no successful replacement was confirmed. Last observed original versions remain state 87, package 30 and report 61. Do not assume pending local updates are in those versions. This checkpoint and the Git audit preserve the new reproducible intake evidence without publishing raw data.

Upstream checked at `7aa7767d1484dce44bb9d250e8c835702074ca8f`: legacy aggregate updates only; not corrected V3 profitability confirmation. Qualified independent OOS mechanisms remain 0.
