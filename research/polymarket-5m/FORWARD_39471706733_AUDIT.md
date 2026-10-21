# Forward run 39471706733 evidence audit — 2026-10-21

## Decision

The recording produced a causally richer BTC bundle, but the strict replay was not
admitted. The workflow's `Require strict BTC replay artifact` step failed because
the frozen source-race dependency fingerprint rejected drift in
`strict_prebuild.py`. This is an admission failure, not a zero-PnL test. The
capture covers 50 mapped and resolved BTC5m markets, not the required common
continuous seven UTC days / 2,016 markets, and its experimental arms use five
shares rather than the ranking target of 100 shares.

Consequently, unified rank7 PnL remains `null`, no ranking row is created, and
the qualified-mechanism count remains zero.

## New strict-bundle development audits

| Family | Evidence | Disposition |
|---|---|---|
| 34 — complementary queue lag | Replay aborted with `ValueError: invalid venue BBO`; the JSON result is empty. | Engineering/data-validation failure. No economic result and no PnL may be imputed. |
| 35 — adverse reclaim | 50 mapped/resolved markets; 7 signals per arm; five-share displayed-depth upper-bound fills. At 500 ms: base 6 fills / 30 shares, +$2.21394; delayed 5s 4 / 20, +$4.17038; shallow 5 / 25, -$6.29212. | Development only, correction denominator 873, promotion explicitly false. Too few signals, variant-selected, not 100 shares, not rank7 and still requires live FAK calibration. |
| 36 — zero-wait source confirmation | 48 opportunities at both 400 and 500 ms. Baseline, confirmation-veto and shifted-placebo arms all sent 0 and filled 0: fixed asks were above limit or confirmation was absent. | A completed no-send diagnostic, not a zero-return backtest. Correction denominator 874; no fill EV exists and promotion is false. |

Family 35's positive base and 5-second arms are reported exactly as development
observations, alongside the negative shallow arm. They are not independent
mechanisms and cannot be promoted or added. Family 36's zero sends are classified
separately from a completed profitable/unprofitable strategy.

## Newly published cumulative reports

These are append-only legacy summaries and still fail the receipt-causal/common
rank7 admission rules already documented in the exchange-clock audit.

| Report | New cumulative result | Audit consequence |
|---|---:|---|
| momentum5s | 1,875 signals; 300/350/400/500 ms: 1,530/1,532/1,532/1,535 fills and -0.46/-1.11/-0.97/-1.02 cents/share | Negative at every displayed lag. Frozen first 1,000 fills at 400 ms remains -0.60 cents/share, 99% day lower bound -4.67 cents, exact p=1. |
| theta30 | 125 orders, 85 persistent-depth fills, 8 days, -0.73 cents/share, p=0.6949 | Negative and below its stopping fill count. |
| seven preregistered confirmations | 13,392 mixed-asset 5m markets; 0 nominal p<0.05, 0 BH, 0 Bonferroni, 0 positive in both halves | No confirmation passes. Best reported #9 has 1,247 trades, -0.03 cents/share, p=0.542. |
| cumulative paper account | 30s: 245 trades / 17,202 shares / -$145.15; 60s: 664 / 53,566 / -$1,629.67 | Not an independent $1,000, 100-share rank7 ledger; descriptive cumulative loss only. |

The run recorded 32 candidate drops attributed to CLOB socket closure and 94
disconnects in the legacy momentum report. Those failures reinforce, rather than
repair, the earlier rejection of future-health filtering and dropped attempts.

## Provenance

- Source result commit: `43581a61b98d36b67f9a6ba2c762311a8fc39a3e`
- Workflow run / analyze job: `39471706733` / `118913842966`
- Converted archive source: 553,007,742 bytes; SHA256
  `d7f55efaf2a0caec223d4e382a2f1155875be47d27569cacf9904ffede168c53`
- Publication manifest SHA256:
  `8127b3ef446aff405051ec957b788e38e058948a6c4a68f765a5ffa99f4f9c0f`
- Family 36 row hash:
  `421ae2d88a10db2084e0db46d3adaa66e9f5b00db99f28a42a8fd13dd99775d2`
- Report blobs: momentum `47237efd2c673c9c731fa0f7d19475ff4f22b193`;
  theta `a31ac2b6ece024414ffc4455d796512f4b6b68f7`;
  confirmation `cc4487c41d4e0ad789dad438513589fb12a30777`;
  paper `b92c53f5223bd5b5576f8b2651a2a1467dd5e327`.
- Live orders, deployment and production-risk changes: none.

## Next valid action

Do not repair the dependency fingerprint and then call the already-observed tape a
formal holdout. Reconcile the strict prebuild/freeze dependency chain, reject
invalid BBO rows at archive validation without selectively removing economic
attempts, freeze new code and inputs, then use a strictly later untouched common
seven-day BTC5m window. Until that exists, Family 35 stays an exploratory hint and
Family 36 stays a no-send diagnostic.
