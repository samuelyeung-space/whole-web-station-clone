# Four explicit USD-M flow variants: signal admission only
2026-10-25 UTC. No historical financial replay.

## Concrete gap and change
The existing native_flow_catalog_join admits NativeFlowTape (spot) only. The separate recorder_futures_flow adapter explicitly rejects spot-catalog adoption and calls for venue-specific registration. This change provides that missing development registration and causal signal entrance; it does not rebuild the flow engine or promote a dollar-budget source-race replay.

New usdm_flow_candidates.py reuses RecorderFuturesFlowTape and mine500_catalog.signal. It registers exactly the first existing parameter set from each template, without choosing by observed PnL:
| New ID | Source ID | Template | Existing threshold | Remaining time |
|---|---|---|---:|---|
| UFLOW-20261025-001 | M500-181 | flow_persistence | 0.15 | [10,30) seconds |
| UFLOW-20261025-002 | M500-191 | flow_acceleration | 0.15 | [10,30) seconds |
| UFLOW-20261025-003 | M500-201 | flow_exhaustion | 0.20 | [10,30) seconds |
| UFLOW-20261025-004 | M500-211 | flow_flip | 0.20 | [10,30) seconds |

All four retain source fingerprints/parameters and get distinct venue-bound rule hashes for binance_usdm BTCUSDT under the pinned recorder contract. They are four registrations within existing order-flow mechanisms, not four independent economic mechanisms. Acceleration is a contrast between normalized15s/60s imbalances, not incremental signed volume.

## Admission and limits
The entry point requires a development RecorderFuturesFlowTape, exact recorder-run receive-clock attestation, causally available15s/60s features and tau computed for that decision. Coverage still needs independent justification; prints, trade IDs or matching clock strings do not establish truth. Missing m/quantity, absent coverage, malformed ingestion, stale tau and future availability never become zero features. A spot tape or OOS split is rejected. Existing parsers and financial evaluators were not modified.

Status is registered_signal_only_execution_unbound. The module evaluates rule direction only: it places no orders, constructs no fixed limit, and does not simulate fees, failures, FAK fills, cash or settlement. Therefore no new financial backtest, executable capacity or profitability is claimed. Full execution-code/input hashes and a later untouched common window remain unbound; the Oct10/11 freezes and Oct12–19/Oct24–31 registrations remain unchanged.

## Validation
Nine synthetic engineering tests passed, including four quote-volume rule-direction fixtures, absence of coverage, missing side, future receipts/coverage preserving the old decision, stale/future tau, wrong clock, actual NativeFlowTape rejection, OOS rejection, corrupt-print failure and distinguishing valid no-signal from missing input. Synthetic fixtures are not historical data or executed strategies.

Registration binds the current signal/flow/parser/catalog SHA256s. Real input hash is null because no real candidate replay input was consumed this round. All720catalog attempts, prior studies and these4registrations remain in the screening history; no denominator reset. Real backtests0, order attempts0, fillsnot evaluated, PnLnull, Rank7null, formal new OOSnull, qualified independent mechanisms0/10.

## Source and next work
Source remains c2d7b38dc4220dfb5f896b92a0474c346ef7d6f7; research parent84cbd167365d6a90d60fe38a21a0c7c6f8819ad7. Latest observed forward run40170142710 is still in progress; newly closed shadow40173069499 has no artifacts. No new economic rows or reserved tape were opened.

Next: obtain source-specific coverage evidence and native quantity/side before admitting real feature rows; then bind fixed-limit partialFAK, market fees/constraints, independentUSD1000/target100shares and500/600/1000ms execution before registering a strictly later common7UTCday2016-slot confirmation window. Until then keep all results pending/null. Preserve queued raw401034 before its2026-10-25T18:39Z expiry, then the remaining raw queue by expiry.

Code commits:04413533ee69b4785d8c494d37432774aad1efce(module),66080955eeb09f0ba7abe7f5057173e80fecaf54(tests),11c1d0b2ac469880882c618f52d9f58468f1c22b(registry). No orders, production changes, deployment, AWS credential access, workflow dispatch or messages.
