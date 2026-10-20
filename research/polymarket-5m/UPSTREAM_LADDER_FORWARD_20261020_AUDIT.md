# Upstream ladder/forward evidence audit — 2026-10-20

## Decision

Source branch commit [`b2f22ab2e052979db732bab3a220b93d4cf34fc9`](https://github.com/yangyangnovelist-hub/whole-web-station-clone/commit/b2f22ab2e052979db732bab3a220b93d4cf34fc9)
adds one completed ladder segment to cumulative daily/4-hour market reports. It does
not add a complete BTC5m rank7 window. No row is admitted to the unified seven-day
BTC5m ranking; rank7 PnL remains `null` and the qualified-mechanism count remains
zero.

## New cumulative results

| Experiment | Current cumulative evidence | Audit disposition |
|---|---:|---|
| NEARCERT taker | 4 buys / 4 settled markets, 1 share each; **-70.78 cents/share**, clustered SE 24.21 cents, t=-2.92 | The earlier one-trade positive observation did not persist. Still below the frozen 300-market/7-trigger-day stopping rule and not a BTC5m/100-share/$1,000 rank7 experiment. |
| NEARCERT 4-hour control | 10 settled markets, **-13.39 cents/share** | Chainlink-settled descriptive control only; explicitly excluded from the candidate decision. |
| CALIB taker | 3 buys / 3 settled markets, **+5.73 cents/share** | Only 3 of the required 40 bought markets, one share each. Positive but extremely small and not rank7 evidence. |
| CALIB maker description | 1 modeled fill, +9.80 cents/share | Descriptive only; no queue/acknowledgement/private fill proof. |
| Non-boundary structural baskets | Deduplicated displayed-depth upper bound about **$2.72/day immediate** and **$0.51/day after 500 ms** | Daily/ladder markets, multiple non-atomic legs, displayed L1 and linear time extrapolation. Not BTC5m single-strategy rank evidence. |

The reports cover 31 ladder segments and 170.4 recorded hours from 2026-10-04
09:29 through 2026-10-20 22:06 UTC. This is accumulated fragmented coverage, not
one common continuous seven-UTC-day recording. The reported universe consists of
daily `above`, `range`, `hit`, daily up/down and four-hour up/down markets,
not the required 2,016 BTC5m slots.

## Newly available source capture

Forward recording run `39471706733` completed its capture and published five raw
parts plus an index. The record job succeeded, but its analysis job was still in
`Resolve, convert and test`; the strict BTC replay artifact and all strategy
steps were pending at this audit. Therefore no PnL, market coverage or health
claim is inferred from the presence of the raw parts alone.

The raw parts total 1,698,657,779 bytes. GitHub lists the index and raw parts as
expiring on 2026-10-21 around 22:06 UTC. Only artifact identifiers, sizes and
GitHub-provided SHA256 digests are recorded below. The signed index download
returned repeated HTTP 503 responses during this audit, so the index contents
were not guessed and the multi-gigabyte archive was not partially downloaded
onto the already-full local filesystem.

| Artifact | ID | Bytes | GitHub digest |
|---|---:|---:|---|
| index | 12303625695 | 4,309 | 32ce79c133ff6cc502e0a327a3cdcb81bfcff7ab32292e047aeb3411727cefa6 |
| part-00000 | 12303490742 | 419,430,662 | 650f87bacbe6908856923be1ee26434b2c7e825f2e4694b5b129407e4402420d |
| part-00001 | 12303520724 | 419,430,662 | 38cf577646c85dc080358e1e6232b490ec5b43810e581b927e423fe00e6647c0 |
| part-00002 | 12303296007 | 419,430,662 | 6d6247f5a90f0d2c3b6522f827ffb4152de9c8521366f6bcba1d25b3ecc87900 |
| part-00003 | 12303585574 | 419,430,662 | 5fecafcf372acb444736b1eefab61571cbef32d7086977f16811977a1a464213 |
| part-00004 | 12303236038 | 20,930,822 | 2297ce30f08750b2e5d4d43c38b55f9e5cbd9191750bb8037f25e0885da72881 |

## Provenance and next action

- Source commit: `b2f22ab2e052979db732bab3a220b93d4cf34fc9`
- Ladder capture run: `39458726419`
- New forward capture run: `39471706733`
- `nearcert-forward.md` blob: `81e853ba1d329c9699310738fbe2682814e97184`
- `calib-forward.md` blob: `94eec7a544dfbde8b5ba8d6c7a2b16d62dfe0904`
- `ladder-check.md` blob: `577c3845e2520172b2802b2bfebf746a780a44e1`
- `ladder-arb.md` blob: `37244e9cbd5693041bc3bc4baae487c7810181b5`
- Evidence class: accumulated non-BTC5m development plus pending raw capture
- Unified seven-day PnL/rank: `null`
- New qualified mechanisms: 0
- Live orders, deployment or production-risk changes: none

A later run should process only the completed immutable outputs of
`39471706733`: verify its index and part digests, require the strict BTC replay
artifact, and then apply receipt-causal, failure-preserving and rank7 coverage gates.
The current in-progress analysis must not be treated as a zero-PnL test.
