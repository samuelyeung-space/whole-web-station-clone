# Family 34 venue-BBO sentinel repair — 2026-10-20

## Finding

The failed Family 34 replay did not expose a crossed executable book. A streaming
scan of the immutable converted archive from workflow `39471706733` examined
the BBO fields attached to strict `clob_price_change` rows. All 549,498 rows
that the old parser would reject used exact venue empty-side boundaries:

- 272,531 rows: `best_ask = 1` with a nonempty executable bid;
- 272,531 rows: `best_bid = 0` with a nonempty executable ask;
- 4,436 rows: both empty-side boundaries;
- zero flagged rows had a finite interior crossed BBO.

For this feed, bid `0` means no bid and ask `1` means no ask. They are
non-executable state sentinels, not price levels. Treating them as malformed made
the old source-branch job terminate before producing Family 34 evidence.

## Repair

`complement_queue_lag.py` now:

1. accepts only the correct empty-side boundary on each side (bid 0, ask 1);
2. still rejects bid 1, ask 0, non-finite values and crossed executable sides;
3. requires an explicit empty sentinel to agree with an actually empty local
   side;
4. invalidates the market and requires a new snapshot when the venue BBO and
   reconstructed local book disagree;
5. counts reported empty bids and asks for auditability.

The repair therefore does not manufacture liquidity and does not silently ignore
the BBO fields. It converts a parser-wide abort into a fail-closed per-market
state check. Eleven direct helper assertions pass, covering both legal sentinels
and illegal boundaries/crosses/non-finite values.

## Evidence boundary

This is an execution-input parser repair only. The already-inspected 50-market
archive remains development data and is not rerun as a fresh holdout. No Family
34 PnL is reported, rank7 PnL remains `null`, and qualified strategies remain
zero. Any formal use requires new input/code hashes and a strictly later untouched
continuous seven-day BTC5m window under the common $1,000 / 100-share protocol.

- Source archive SHA256:
  `d7f55efaf2a0caec223d4e382a2f1155875be47d27569cacf9904ffede168c53`
- Old source-branch implementation blob:
  `cd1de7892f9478ea9b2c04e92193f8677f8de449`
- Research-branch pre-repair blob:
  `64738f7469673d2d891df3871a86c92df703574e`
- Live orders, deployment and production changes: none.
