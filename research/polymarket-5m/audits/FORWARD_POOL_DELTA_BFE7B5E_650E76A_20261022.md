# Forward pool delta audit: bfe7b5e → 650e76a (2026-10-22)

## Scope and classification

- Source branch: `claude/polymarket-options-strategy-1lc7i4`
- Parent: `bfe7b5e9680ad39e9b47997001a793c7f4980357`
- Head: `650e76a2e792ab8bbc8992d6b38a6341648bd0fe`
- Source workflow: run `39685165486`
- At audit time, the recorder job had succeeded but the analyzer job was still running. The strict BTC replay step failed before producing an admissible PnL result; later pooled-family steps continued.
- Classification: **new partial-day pooled development evidence**, not a seven-day ranking run and not formal OOS evidence.

## Raw-capture transport manifest

The run published five payload parts plus an index. The index declares:

| Field | Value |
|---|---:|
| Source bytes | 1,729,976,320 |
| Source SHA-256 | `fa509b1c992b26ca50986cd494e63a5b5594274c4d1180dd744d4d6d419ff2e9` |
| Payload parts | 5 |
| Part sizes | 4 × 419,430,400 + 52,254,720 |

The workflow's restore/verification step succeeded. This audit independently inspected the small index, but did **not** redownload and rehash all 1.73 GB payload parts. The manifest is therefore transport evidence only; it does not establish complete BTC5m day coverage, label correctness, or economic validity.

## Semantic append audit

CSV rows were normalized numerically to 12 significant digits before comparison, because the new commit canonicalized Python float text (for example `0.002688000000000003` to `0.002688`). Byte-level files are therefore not append-only, but no previously published semantic row disappeared at this tolerance.

| File | Old rows | New rows | Old semantic rows missing | New semantic rows |
|---|---:|---:|---:|---:|
| `markets.csv` | 14,840 | 15,085 | 0 | 245 |
| `momentum5s.csv` | 6,704 | 6,884 | 0 | 180 |
| `paper_ledger.csv` | 1,009 | 1,021 | 0 | 12 |
| `theta30.csv` | 1,968 | 2,017 | 0 | 49 |

GitHub compare reports 301 additions and zero deletions for `trades.csv`; this audit did not complete an independent semantic parse of that large file.

## Momentum5s delta

The new data contain 45 signal rows at each of four match lags. With the frozen five-share accounting:

| Match lag | Incremental fills | Incremental net PnL |
|---|---:|---:|
| 300 ms | 39 | +$8.07776 |
| 350 ms | 39 | +$8.08840 |
| 400 ms | 38 | +$6.65273 |
| 500 ms | 38 | +$10.96302 |

These are partial-day development increments and are not independent strategy discoveries. Cumulative results remain negative and fail the frozen test:

| Match lag | Cumulative fills | Mean net edge/share | Adjusted p |
|---|---:|---:|---:|
| 300 ms | 1,784 | -0.30¢ | 1 |
| 350 ms | 1,786 | -0.91¢ | 1 |
| 400 ms | 1,788 | -0.76¢ | 1 |
| 500 ms | 1,792 | -0.79¢ | 1 |

The frozen first-1,000-fill subset remains failed at -0.60¢/share with a 99% day-cluster lower bound of -4.67¢/share.

## Pre-registered confirmations

All seven pre-registered pooled candidates remain failed. The least negative candidate, #9, has 1,408 trades, -0.50¢/share, raw p=0.751 and Bonferroni p=1. The other candidates range from -0.80¢ to -5.43¢/share with corrected p=1. Candidate #101 remains sparse (28 trades, -2.37¢/share), and the September on-chain group also remains negative; #109 has only 254 fills, below its 2,000-fill stopping point.

## Theta30 and paper ledger

`theta30` added 49 rows, three sent orders and three simulated fills. The incremental net PnL was +$1.40914; cumulative fills rose to 97 and cumulative reported edge to +0.27¢/share. The lower confidence bound is still unavailable and the candidate remains insufficiently sampled.

The paper ledger added:

| Horizon | Incremental trades | Incremental shares | Incremental net PnL |
|---|---:|---:|---:|
| 30 s | 3 | 255.00 | +$24.570065 |
| 60 s | 9 | 767.19 | -$53.553933 |

The upstream report's “per-share PnL” is an equal-weighted per-trade statistic, not `total PnL / total shares`. For capacity/cash accounting, the cumulative share-weighted values are approximately:

- 30 s: $0.26 / 19,966 shares = **+0.0013¢/share**, not +0.40¢/share.
- 60 s: -$1,983.58 / 60,147 shares = **-3.2978¢/share**, not -2.88¢/share.

## Rank7 and OOS decision

- Complete newly admitted UTC recording days: **0**
- Exact common seven-day / 2,016-market coverage: **not available**
- Rank7 PnL: **null**
- Formal OOS result: **null**
- Confirmed high-edge distinct strategies: **0 / 10**

No simulated positive partial-day increment is promoted to the unified ranking. A newer recording run (`39710494768`) was still recording and had no artifact at audit time.

No live order was submitted and no production position, risk limit, or deployment setting was changed.
