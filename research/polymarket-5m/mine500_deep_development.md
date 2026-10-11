# Multi-archive development experiments

This lane extends consumed October development data. It does not modify the
original 720-rule freeze or spend its October 12-19 confirmation window.

## Fixed experiments

`mine500_path_candidates.py` defines 32 rules: eight path templates, two
lookbacks and two remaining-time bands. Crossing order, occupation, extrema
and variation concentration add information beyond endpoint returns. They
remain overlapping price-derived hypotheses, not 32 independent mechanisms.

`mine500_probability_models.py` defines eight trials: the raw market-midpoint
control and three fixed-offset residual specifications, each at two explicit
ask-plus-fee margins. Training uses only earlier UTC dates and labels actually
received before the next day's cutoff. Each market has total weight one.

`mine500_deep_batch.py` registers every trial, execution scenario and input/code
hash before fitting or replay. The known multiplicity lower bound is 860:
720 old rules, 40 new trials and the existing 100-rule zoo; other historical
adaptive screening is additional. A reference replay of the old 720 rules
on expanded data does not add 720 new strategies.

## Receipt and execution contracts

Use `development_adapter_v2.py` only for historical development archives. It
retains the first received optional metadata; a later non-null configuration
must not be moved into the past. Mapping, fee and conflicting explicit market
definitions remain errors. This adapter cannot inherit the old OOS freeze.

`mine500_constraint_extract.py` recovers minimum size from locally received
Gamma records and token-specific ticks from CLOB snapshots or explicit tick
changes. Legacy strict archives omitted tick-change messages. Snapshot checks
are necessary but cannot prove complete dynamic constraint coverage.

`mine500_replay_v2.py` preserves the first signal even when rejected. Limits
are fixed at decision time; partial FAK, failed matching, latency, fees and
cash occupation remain in the ledger. Total decision-to-match latencies are
500, 600 and 1000 ms; venue waiting is already included. Decisions and matches
must belong to the same observed interval. An unrecorded gap cannot supply
liquidity. Settlement cash is released only when the official label is received.

Sizes 5/10/20, 100/500-share capacity stress, half displayed depth, an additional one-cent cost per filled
share and an assumed one-dollar minimum are separate counterfactual scenarios.
The latter three are sensitivity assumptions, not estimates of real execution
and not statements of the venue's current minimum. The aggregate-order fee
proxy rounds to five decimal places, with raw fees below 0.00001 treated as
zero; HALF_UP ties and aggregation across underlying maker trades are explicit
assumptions. Actual fee granularity remains uncalibrated. Raw fees and rounding
differences are retained. Displayed depth never proves actual fills.
The larger share requests test displayed capacity and whether a shared $1,000
account binds; they are not evidence such orders would achieve those fills.

The outcome-blind shared portfolio takes the earliest signal among all 40 new
rules, breaks exact-time ties by candidate ID and allows only one attempt per
market. Missing execution horizons or unresolved fills block complete portfolio
PnL. Alternative latencies, sizes and portfolios must never be summed.

## Reproduction

The input files and private result package are held separately from this public
code repository. Each input must be a disjoint consumed development recording
with receipt-clock decisions and exact 500/600/1000 ms book projections.

```sh
python mine500_deep_batch.py --inputs /private/recording1.jsonl.gz /private/recording2.jsonl.gz --out /private/new-run-directory
```

The output directory must not already exist. Registration, probability-fold
diagnostics, all attempts, missing-data statuses, daily summaries, cost/capacity
alternatives, shared cash and old-rule transport diagnostics are retained.
Synthetic tests are engineering checks only. Positive development PnL is not
an independent OOS result or a qualification for real trading.

## Execution receipt calibration correction

The original private FAK calibration schema incorrectly shared per-share PnL
between displayed and actual fills and used independent-order intervals. Its
output is invalid for evidence. The revised schema separates VWAP and fees,
keeps unknown/rejected receipts without inventing a match time, and uses whole
day clusters only when enough distinct days and markets are available. No
live-order capture or authenticated trading operation is included here.
