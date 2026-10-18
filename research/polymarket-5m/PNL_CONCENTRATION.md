# Existing-ledger PnL concentration

Descriptive audit of the complete35-rule,315-cell common development panel.
No strategy, rule, model, execution, account path or frozen protocol changes.
This performs **zero new replays**, fits nothing, and does not create new trials,
independent samples, OOS evidence or seven-day rankings.

Inputs are the prior `metrics.json`, per-attempt `market_metrics.json`, original
explicit336-market UTC-start calendar, and common-panel protocol. All315 cells
must remain; missing scenarios, ambiguous evidence categories, unmatched
identities, missing settled PnL, count/status/quantity mismatches fail closed.
Only explicitly already-consumed development is accepted. Every input byte hash
and the code hash are retained. No quote or protected outcome tape is read.

Each account remains its original independent$1000,target100shares path.
The module sums existing net contributions by market **before** concentration
measurement, then by the explicit market-start UTCdate. It reports the largest
positive block, positive-contribution HHI, fraction of gross positive PnL and
fraction of net PnL(the latter can exceed100%), fixed top1/3/5 contribution
removal arithmetic, and how many largest positive blocks erase attributed net.
Removal arithmetic sums the remaining ledger contributions with `math.fsum`;
it never leaves summary-versus-ledger roundoff as apparent residual profit.
Original summary PnL is retained alongside the explicit attribution difference.
All500ms rule pairs receive separate realised-vector equality and nonzero-PnL
market overlap diagnostics for each scenario;latencies/scenarios are never added.

Deleting realised contributions does **not** recompute cash-constrained orders:
released capital would have changed later eligibility. Therefore the deletion
columns are arithmetic fragility attribution, not executable leave-day-out
backtests or drawdown/stress estimates. Exact historical vector equality does
not prove future rules identical. HHI is descriptive, not an independent
mechanism count. Few partial days and adaptive screening do not support a
defensible day-cluster significance claim; confidence intervals,p-values and
formal OOS results stay null. The720catalog/history denominator is unchanged.

Reproduce against the unchanged already-consumed files:

```sh
python -m unittest test_pnl_concentration -v
python pnl_concentration.py --metrics PRIOR_PANEL/metrics.json \
  --attempts PRIOR_PANEL/market_metrics.json --coverage MFA_V4/shared/coverage.json \
  --protocol PRIOR_PANEL/protocol.json --out NEW_DIRECTORY
```

The audit produces all315 attribution cells, per-market/per-date tables, all1785
main500ms pairs, input/code hashes and explicit warnings. These are new analysis
outputs from existing ledgers, not315 new backtests. Private results stay outside
the public source repository. Full calendar coverage and strict future evidence
remain required before the user-facing seven-day ranking.
