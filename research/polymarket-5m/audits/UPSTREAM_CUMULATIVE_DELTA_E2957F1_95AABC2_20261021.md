# Upstream cumulative-report delta audit: e2957f1 → 95aabc2

As of 2026-10-21T22:49:53Z, workflow run 39670989642 completed successfully and committed source revision `95aabc2e5fb8be50ce2c0fb39f68073c755183b4`. It published no workflow artifact.

The source commit adds cumulative diagnostics for recording run 39614816930 as the 94th segment. It is development evidence only. The underlying recording covers about four hours and zero complete UTC days, so it is not Rank7 or formal OOS evidence.

## Fail-closed immutability result

The cumulative reports are not append-only. Existing records for historical run 38909708508 changed again between source revisions e2957f1 and 95aabc2:

- Test F dropped-candidate count: 1720 → 1694.
- Test G dropped-candidate count: 1728 → 1697.
- Test I dropped-candidate count: 3582 → 3554.
- Recorder urgent-move count: 546 → 545.
- Recorder +1000ms reaction percentage: 78% → 79%.
- Recorder first-move summary: 251 / 1676 ms → 255 / 1703 ms.

This reverses values that had changed in the prior cumulative update. Because prior-run records are mutable, the new cumulative tables are rejected as immutable Rank7/OOS input. Frozen stopping verdicts are not recomputed.

## New cumulative diagnostics

- Test C at 300ms: 3,774 trades, -0.77 cents/share, p=1.0000.
- Test D at 300ms: 2,530 trades, +1.52 cents/share, p=0.0447; the frozen first-1,200 verdict remains failed.
- Test F: 2,409 trades, +4.72 cents/share; frozen stop unchanged.
- Test G: 1,184 trades, +11.44 cents/share; frozen stop unchanged.
- Test I at 400ms: 1,840 trades across 1,220 markets, +8.39 cents/share; frozen stop unchanged.
- Test J at 500ms: 1,926 sends across 1,211 markets, +1.57 cents per ordered share, p=0.0150. At 600ms it is +1.28 cents, p=0.0361. The registered first-1,000-market verdict remains unchanged.

No new financial replay was performed by this audit. Rank7 and formal OOS remain null; confirmed qualified mechanisms remain 0/10. No orders were submitted and no production settings or positions were changed.
