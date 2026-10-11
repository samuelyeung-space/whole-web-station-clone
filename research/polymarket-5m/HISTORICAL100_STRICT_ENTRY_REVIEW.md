# Historical 100-share entry-policy review

This is a research audit. It does not place orders or change production risk.

## Rejected comparison run

GitHub Actions run `38140907351` completed the frozen R3 replay over 21
historical days, but its execution engine treated 100 shares as a maximum FAK
request. It therefore admitted partial entries. The output is not comparable
with the required policy of exactly 100 filled shares per successful entry.

The three latency alternatives reported 158, 134, and 79 partial entries at
500, 600, and 1000 ms. They also ended with 44, 43, and 36 unresolved positions.
Consequently, `net_pnl_usd` is null in every summary. The positive
`realized_pnl_usd` values are incomplete realized-only amounts and must not be
presented as the strategy's net PnL.

## Corrected execution rule

The replacement run keeps the frozen signal rows, ordering, thresholds, fixed
decision-time limit prices, archive identities, market data, $1,000 account,
fee formula, and 500/600/1000 ms total decision-to-match alternatives. It
changes only entry execution:

- request exactly 100 shares;
- fill all 100 at the fixed limit or reject the whole entry;
- do not consume displayed depth when the atomic entry is rejected;
- never resize, retry, borrow, or use future depth;
- continue to record failed attempts and unresolved inventory explicitly.

Because execution semantics changed, the continuation audit records the old
and new engine hashes separately. The corrected run remains historical
capacity revalidation, not new untouched OOS evidence.
