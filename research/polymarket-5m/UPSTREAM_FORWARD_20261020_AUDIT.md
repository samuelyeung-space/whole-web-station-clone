# Upstream legacy forward audit — 2026-10-20

## Scope

This note audits only the newly published result commit
[`f875d34ebc20ac8c2b9032fc637ce0c5b035cd99`](https://github.com/yangyangnovelist-hub/whole-web-station-clone/commit/f875d34ebc20ac8c2b9032fc637ce0c5b035cd99).
That commit changes result ledgers/reports under `research/polymarket-5m/forward/`; it
does not change recorder or strategy code. This audit does not replay the tape, change
a frozen rule, or admit any row to the unified seven-day ranking.

## Newly published evidence

| Evidence | Published result | Audit disposition |
|---|---:|---|
| Frozen legacy `momentum5s`, primary 400 ms, 5 shares | First 1,000 fills over 8 trigger days; net EV **-0.60 cents/share**; one-sided 99% day-cluster lower bound **-4.67 cents/share**; exact test **p=1** | The exact old frozen configuration fails its own stopping gate and is retired from promotion. |
| `momentum5s` 500 ms sensitivity, 5 shares | 1,501 fills over 10 trigger days; net EV **-0.92 cents/share**; exact test **p=1** | Descriptive legacy sensitivity only; not the unified 100-share/$1,000 cash-account main scenario. |
| Frozen legacy `theta30` | 122 orders, 82 persistent-depth fills (67.2%) over 8 trigger days; net EV **-0.99 cents/share**; exact test **p=0.7252** | Remains insufficient-fills/pending. It is not evidence of a profitable strategy. |
| Legacy cumulative confirmation set | Seven preregistered rows: no raw p-value below 0.05, no BH/Bonferroni discoveries, and none positive in both halves | All published rows fail promotion, but the multi-asset, one-share, cumulative sample is not the common BTC5m seven-day ranking. |

The latency sensitivity rows published for `momentum5s` are all negative at 300,
350, 400 and 500 ms. Those rows use the old protocol and 5-share sizing, so they
cannot be substituted for the current ranking contract: one fixed complete
seven-UTC-day BTC5m market set, 2,016 scheduled markets, independent $1,000
accounts, 100-share targets, 500 ms main scenario, and separately reported
600/1,000 ms and cost/depth stresses.

## Coverage and health limits

The associated run-39435891649 metadata covers 2026-10-20 14:04–18:04 UTC, not a
complete seven-day window. The retained health evidence reports long writer-enqueue
ages for RTDS and RTDS-Binance (about 79m56s and 100m10s maxima respectively);
CLOB-BTC and Binance-strict did not show the same >30 s condition. Writer-enqueue age
is not source-event age, but it is enough to block a clean claim that all required
external inputs were continuously fresh. The complete 2.317 GB archive was not
recovered, hashed and durably retained in this research environment; the partial
metadata sidecar has SHA256
`b32f1e57e6579f11b7f20c702e0075e284f11d68d70318ae999786114f8d8a5c`.

Consequently, the negative first-1,000-fill result is used only to reject promotion
of the exact old `momentum5s` configuration under its own frozen test. It does not
prove that every momentum or theta mechanism fails, and it is not formal OOS
confirmation under the current rank7 contract.

## Provenance

- Source commit: `f875d34ebc20ac8c2b9032fc637ce0c5b035cd99`
- `forward/momentum5s.md` blob: `7b85e0e10ea11ed4f27761419d72706a31cafdd7`
- `forward/theta30.md` blob: `2ef398be890422c7f3a0a64adf5c03ac27122f2`
- `forward/confirm.md` blob: `918e682325bf94e0ad0d4bb25558abd2c03eb5e0`
- Evidence class: legacy frozen forward gate / development audit
- Unified seven-day PnL and rank: `null`
- New qualified mechanisms: 0
- Orders submitted or production settings changed by this audit: none
