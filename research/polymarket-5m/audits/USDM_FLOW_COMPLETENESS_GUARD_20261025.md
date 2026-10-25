# Incomplete decision inputs must not imply a complete development replay

2026-10-25 client-date research audit. Synthetic counterexample only; no historical strategy replay.

## Reproduced issue

Using the existing USD-M fixture and actual unchanged bridge/FAK engine, add a second market whose decision lacks a binding. The first market has valid native-flow/tau inputs and a synthetic settled fill. The second has unknown features. The engine increments invalid_decision_reasons but can still label the known first-market contribution simulated_complete and return a numeric net_pnl_usd. At 500ms, UFLOW-001 reproduces this mismatch. The counterexample also runs the existing three latency/depth/cost alternatives; these are synthetic checks, not fresh historical backtests.

The engine only examines decisions before a candidate's first qualifying signal consumes that market. The new guard uses precisely those recorded invalid-decision counters. It does not use later health to delete earlier fills, and does not choose a profitable subset.

## Added checked execution path

```bash
python3 usdm_flow_checked_replay.py /consumed-development/bundle/manifest.json \
  --sha256 ACTUAL_MANIFEST_SHA256 --output /scratch/checked-development.json
python3 -m unittest test_usdm_flow_checked_replay -v
```

The new entry calls the previous hash-bound bundle loader and existing execution bridge. It returns admitted_development_summary separately from raw_development_diagnostics. For each candidate/scenario/latency, any nonzero pre-consumption invalid_decision_reasons changes the admitted status to incomplete_invalid_decision_inputs and withholds net_pnl_usd and per-share PnL as null. Known attempts and partial ledger/cash/drawdown figures remain only in the nested raw diagnostic. Missing/malformed/negative/noninteger counters are rejected instead of assumed zero. Complete synthetic inputs retain development-only PnL; this is not an OOS or ranking promotion.

This is a conservative completeness rule: it can withhold a result whose omitted invalid decision would ultimately not have signaled. Without valid inputs that fact cannot be established causally. No selection based on sign or existing fill count is permitted. The original result is not mutated, no input is repaired and no fills are recalculated. Original bridge/bundle APIs and old execution modules remain unchanged for reproducibility. Future reporting of this flow lane should use the checked entry; direct use of the older APIs still exposes their original raw summary behavior.

This guard is deliberately narrow. Zero invalid counters do not prove that all expected decisions were supplied, all markets were recorded, coverage attestations were truthful, quotes/fees were complete, or displayed depth would have filled. Missing entire market/decision records may not appear in the counters. Independent coverage and full rank7 admission are still mandatory. Python execution remains unsealed. No old frozen protocol is inherited.

## Validation and scope

Five new tests passed: actual-engine two-market counterexample; complete fixture preserves only development PnL; identical veto for zero-fill and negative synthetic PnL; malformed counters rejected; file entry applies the audit. The earlier seven file-loader checks and six bridge checks were not repeated as new progress. Synthetic monetary values are not reported as returns.

No real input was read, bound or replayed. Known 400040 corruption and missing independent native coverage remain unresolved. No historical result, including M500-439, was recomputed or revised by this counterexample. A separate historical input audit would be required to establish whether the failure mode occurred there.

New candidates: 0. New financial replays: 0. New full UTC days admitted: 0. Rank7 PnL: null. Formal OOS: null. Qualified independent mechanisms: 0/10. Existing attempts and selection denominators stay unchanged.

Source head 2c13d5e71f3b879f7d7f8ee291dc5cf3e165e8c5 and research parent e2efe0da92f60239fdc573dee356d8b778c12c1c were unchanged. Forward40222949001 has no published artifacts at this metadata check; shadow40228763386 remains in progress. No full logs, market tape or new financial outputs were opened. Raw402038 is already preserved and was not downloaded again. No orders, AWS access, deployment, production edits or workflow dispatch.

Next: use checked entry with legitimate already-consumed development inputs and independently justified receipt coverage; retain pending/null until those inputs exist. Preserve reserved Oct24–Oct31 diagnostic content, prior exposure record and old Oct10/11 plus Oct12–19 freezes. Rank7 still requires seven complete consecutive UTC days and 2016 slots, independent $1000 per strategy/scenario, target100shares and all fixed execution/cost stresses. Future true OOS requires a separate strictly later untouched registered window.
