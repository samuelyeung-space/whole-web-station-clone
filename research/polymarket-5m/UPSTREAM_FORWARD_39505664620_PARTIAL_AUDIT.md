# Forward run 39505664620 partial evidence audit — 2026-10-21

## Decision

The new raw recording is byte-complete and has been recovered before its Actions
artifacts expire. It still does **not** create a rank7 row. The published BTC result
slice contains 48 consecutive five-minute starts from 2026-10-20 22:15 UTC through
2026-10-21 02:10 UTC: four hours, zero complete UTC days, versus the common
seven-day requirement of 2,016 markets. The workflow analysis was still running at
the audit checkpoint, its strict-BTC-artifact gate had failed, and Family 36 had not
finished. A failed admission gate is not zero PnL.

Unified rank7 PnL therefore remains `null`; qualified independent mechanisms remain
0/10.

## Raw-capture recovery and health

- Run `39505664620` was reconstructed from five independently downloaded parts.
  All part, payload, member and gzip CRC checks passed.
- Reconstructed archive: 1,833,512,960 bytes, SHA256
  `03153749711a51bec31ef42858e1d4289bcc5d047c221b78535961c7fbe7a325`.
- Capture span: 14,410.018 seconds; 38 checkpoint files; 50 registered BTC5m
  market slugs. Registration is not quote/outcome/execution coverage.
- The capture declares `complete_feed_health_verified=false` and an unverified
  collector region. At capture end, RTDS was 1,727.335 seconds old and
  RTDS-Binance was 4,904.480 seconds old; BTC CLOB, Binance-strict and Deribit
  were about ten seconds old. Any candidate requiring the stale RTDS feeds is
  inadmissible without a later clean recording.

The detailed read-only integrity record is
`capture_archive_integrity_39505664620.json`. No price or outcome was used by that
integrity scan, and the scan performed no financial replay.

## New BTC paper ledger

The raw capture contains 16 settled paper trades. These are variable displayed-depth
fills, not actual FAK receipts and not an independent $1,000 rank7 account.

| Decision horizon | Trades | Wins | Filled shares | Net PnL | PnL / filled share | Upstream displayed “per share” | ROI on cost |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 30 s | 4 | 4 | 365.00 | +$22.115082 | +6.059¢ | +6.101¢ | +6.45% |
| 60 s | 12 | 10 | 900.07 | -$78.557949 | **-8.728¢** | -4.986¢ | -9.89% |

Combined run-local PnL is **-$56.442867**. The positive 30-second four-trade row
and negative 60-second row are different horizon variants and cannot be added as
independent strategies.

The difference between the two per-share columns is a newly identified accounting
issue: upstream `paper_trader.py` averages each trade's `PnL / shares` equally.
That is not realized PnL per filled share when sizes vary. The executable audit
`paper_ledger_weighting_audit.py` retains the upstream value for reconciliation but
uses `sum(PnL) / sum(shares)` for capacity/economic reporting. Three fail-closed unit
tests pass.

The same correction changes the cumulative 30-second paper row from the displayed
-0.164¢ to **-0.700¢ per filled share**, and the cumulative 60-second row from
-2.755¢ to **-3.136¢ per filled share**. Total cumulative PnL is unchanged:
-$123.035667 and -$1,708.224502 respectively.

## Updated legacy stopping reports

| Lane | Newly published cumulative result | Disposition |
|---|---:|---|
| `momentum5s`, 500 ms / 5 shares | 1,921 signals, 1,573 fills, 11 trigger days, -0.96¢/share, exact p=1 | Negative. Its frozen first-1,000 400 ms verdict remains terminal at -0.60¢, 99% day lower bound -4.67¢ and p=1. |
| `theta30` | 128 orders, 87 persistent-depth fills over 8 trigger days, -0.59¢/share, p=.6795 | Still below its 100-fill gate and not positive evidence. |
| Seven preregistered mixed-asset rules | 0 nominal p<.05; 0 BH/Bonferroni; 0 positive in both halves | Best rule #9 worsened to -0.27¢/share on 1,264 trades, p=.643. |
| Rule #101 | 28 trades, -2.37¢/share, p=.739 | Unchanged failure to reproduce. |
| Rules #103–#108 | Every published row negative | No promotion. |
| Rule #109 | 238/2,000 fills, -2.39¢/share | Not judged, but currently negative. |

These are cumulative legacy or mixed-asset reports, not the common BTC-only,
100-share, 500/600/1,000 ms, independent-$1,000 seven-day experiment.

## Provenance and next gate

- Recording source commit: `56b2a704f975d5a6ecea5dd5d827ad62334c1cb5`
- Published result commit: `cec4d40e9f84d60c3d1b49b605ae5d0aa2f972f7`
- Transport manifest SHA256:
  `a112b5c9efc5974970e119fc931b2bd6a8fbb0ac4ab1d4686cb93d3b50f79166`
- Run-local ledger SHA256:
  `cfd199154ad8394226afde093504d1306f6f2c91d967a72bccc60bc084d8d953`
- Frozen rules changed by this audit: no
- Orders / production changes: 0 / none

Wait for the terminal analysis result and process only newly published Family
34/35/36 outputs. Do not replay this now-observed four-hour tape as fresh OOS.
Future rank7 admission still requires one pre-registered common continuous week,
complete execution inputs and official outcomes, all attempted sends/failures,
100-share targets, independent $1,000 cash ledgers, and the fixed cost/depth grid.
