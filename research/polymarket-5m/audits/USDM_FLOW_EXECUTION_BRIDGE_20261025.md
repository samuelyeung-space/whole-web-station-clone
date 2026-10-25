# USD-M flow development execution bridge
2026-10-25 UTC. Synthetic integration only; no real candidate PnL.

## Change
usdm_flow_execution_bridge.replay_development connects the four already registered UFLOW-20261025-001..004 rules to unchanged mine500_replay_v2.replay_events. No second matching or cash engine was created. Original recorder tapes and current-decision tau evidence are supplied through unique market/receive-time bindings. The existing causal signal adapter supplies feature values and availability timestamps. Missing bindings remain explicit audit blockers. Caller scalar flow, duplicate decisions/bindings, wrong clocks and non-development headers/scope are rejected. Blocked fields remain null rather than zero. Features, books, limits and official outcomes are still separately handled by the existing execution engine.

Each of base,half-depth and extra1cent scenarios runs all500/600/1000ms total-latency buckets, with separate$1000cash per candidate/latency/scenario and exactly100requested shares, required market constraints and partialFAK behavior. The existing engine consumes a market at its first qualifying signal, freezes the observed selected-side ask limit, accounts for fees/failures and recycles cash only under its existing settlement logic. Total latency includes venue waiting; no additional waiting is added. The engine's250msdefault quote-age bound is a quote freshness setting, not venue taker waiting. No historical market fee or tick parameter was inferred from synthetic fixtures.

## Evidence
Six synthetic integration tests passed. One full-chain fixture exercises the four registered candidate slots across9execution alternatives each; only triggered candidates create simulated attempts. It verifies100requested shares, unchanged0.50limit despite cheaper later quotes,40share partialfills,20share half-depth partialfills, all3matching-time offsets, positive synthetic fees and settlement. Other checks cover missing native coverage, missing binding, future tau availability, scalar flow override/duplicate bindings and OOS/wrong-clock rejection. These are36synthetic summary cells, not36historical financial backtests or new catalog trials. No synthetic PnL is presented as strategy performance. Test run used the same source semantics as the verified canonical execution and constraint Git blobs; exact dependency hashes are retained.

## Limits and use
This is an importable development API, not a deployed collector or frozen OOS runner. Example in the repository's research/polymarket-5m environment:

```python
from usdm_flow_execution_bridge import replay_development
result = replay_development(normalized_events, decision_bindings,
    split='development', input_evidence_class='consumed_development')
```

Each binding contains market_id,decision_recv_ms,tape,capture_clock_id,tau_evidence. The tape must be an existing RecorderFuturesFlowTape. Normalized input must provide causal dataset/market/book/decision/official-outcome/constraint records required by the unchanged engine. Decisions are rebuilt from the native binding; caller-enriched flow scalars are prohibited.

Bindings, coverage records and the consumed-development classification are caller attestations, not independent verification. This bridge does not certify their truth or provide immutable/sealed input execution. Displayed-depth fills remain capacity upper-bound simulations. It always reports independent_coverage_truth_verifiedfalse,real_fills_verifiedfalse,main_ranking_admittedfalse,Rank7PnLnull and formalOOSnull. Real input/tape identities and independent coverage proof remain unbound. The known400040bad-price input is not repaired or rerun here. The four rules/fingerprints are unchanged; this is a new development execution lane requiring its own later registration, not an amendment to Oct10/11 or Oct12–19 freezes.

Source and research parents were unchanged at inspection. Newly published raw40203834265 has3parts plus index, expiry starting2026-10-26T18:47:42Z; only metadata was read and it is queued for preservation. No raw market, PnL or reservedOct24–Oct31content was opened. Real financial replays0,new candidates0,new mechanisms0,fullUTCdays admitted0,qualified mechanisms0/10. Existing720and historical selection history retained. No orders,production changes,AWS access,deployment,workflow dispatch or messages.
