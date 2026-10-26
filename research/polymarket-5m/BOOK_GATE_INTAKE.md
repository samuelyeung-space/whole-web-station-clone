# Receipt-group quote intake

The orderbook-entry protocol is unchanged: freeze each parent's original first
signal and evaluate balance/pressure repair or displayed-liquidity-vacuum repair.
Fixed price discounts remain controls. This adapter is quote-only development
infrastructure; a gate is not an executed order, a queue fill or a profit result.

`atomic_quote_groups.py` reuses `absorption.TokenBook` validation and commits one
complete `(connection_epoch, recv_ms)` group. Normalized row sequence does not
prove physical wire-frame identity. Multiple assets at one timestamp and redundant
same-level rows do not create extra persistence observations. Conflicting
same-level values, mixed snapshots/deltas or multiple snapshots invalidate gate
continuity. A temporary cross inside a group is never exposed. Final crosses,
invalid boundary deletes, missing snapshots and source lifecycle changes fail
closed; only a later valid snapshot can recover an invalid book.

`run_book_gate_intake.py` consumes a protocol containing input/code hashes and
preselected parent signal times. A parent arm precedes equal-time book updates;
source timestamps never align decisions. The bought token has direction +1 even
when the parent BTC signal buys Down. It warms the quote baseline only from
received data, expires on observed local time, and leaves EOF watches censored.
There is no native aggressor flow or actor/cancel attribution. PM L2 and external
BTC L2 remain distinct data requirements.

Run with `python run_book_gate_intake.py --protocol INPUT.json --output AUDIT.json`.
Use `python -m unittest test_atomic_quote_groups test_book_balance_vacuum` for
the atomic/lifecycle/quote-gate checks. The caller must register a consumed-data
cohort before execution and retain all rejections, including missing baselines.

## Financial integration still required

Generate independent quote projections at the new off-grid gate decision and
500/600/1000 ms matching times, retaining per-token receipt clocks, lifecycle and
as-of tick/minimum-size/fee metadata. The existing one-second decision projection
cannot be relabelled for these arbitrary times. Apply fixed-at-decision limit,
partial/failed FAK, shared cash and consumed-depth ledgers with the existing replay
engine. Compare WAIT100, 25+75, 50+50 and 100+100 against corresponding immediate
100/200 controls; fractional legs are auxiliary allocation studies. Include missed
runaways and adverse selections. No execution result or new ranking is produced
by this intake. A future confirmation interval requires a separate complete
pipeline freeze and strictly later untouched data.
