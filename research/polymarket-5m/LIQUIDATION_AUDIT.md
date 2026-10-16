# Indicative liquidation-risk audit

`replay_liquidation_audit.py` reads a completed assessment, original engine cash
events and the matching canonical normalized tape. It does not rerun strategies,
change fills, fit a model, select parameters, or consume a new date. Run all
base500/600/1000 isolated and shared accounts; never choose only a winner.

Positions open on actual recorded engine fill events and disappear on recorded
settlement events. Wealth includes cash (including reservations), locked known
payout receivables, and indicative liquidation inventory. Available cash stays
cash minus reservations. These three concepts must not be conflated.

Only native selected-token bid levels with their own local receipt clock,
health and recorded fee rate can mark inventory. No complement reconstruction,
midpoint backfill, future label, or last-good stale quote is allowed. Aggregate
same-market/same-side positions before consuming displayed depth. Unknown,
unhealthy, stale, closed-market, or depth-short shares remain an explicit
0..1/share range; they are not booked as zero-valued holdings.

Audit after same-timestamp groups and all cash events. Native quote expiry
(250ms plus the next representable instant) and market close also create audit
times, so a quote cannot stay fresh over an otherwise silent period. Output
only account-relevant changes; counts are snapshot counts, never time-weighted
coverage. Input receipt order must be nondecreasing. An uncertainty interval at
one instant cannot supply both the peak and trough for drawdown at that instant.

Report conditional quote-model drawdown low/high, missing mark fractions,
unknown inventory widths and terminal cash reconciliation. These are NOT
statistical confidence intervals, guaranteed liquidation proceeds, actual
mark-to-market risk, or executable exit backtests. No sell-side wire latency,
minimum quantity/notional/tick gate, market impact or actual FAK feedback is
modeled. The original aggregate-order fee-rounding proxy remains disclosed.

```
python replay_liquidation_audit.py --events /path/to/cache/events.jsonl.gz \
  --assessment /path/to/completed/assessment.json --output /new/audit/path
python -m unittest test_replay_liquidation_audit -v
```

Preserve exact earlier model/economic code hashes and all frozen windows.
Private tapes, cash events and valuation snapshots stay out of the public repo.
