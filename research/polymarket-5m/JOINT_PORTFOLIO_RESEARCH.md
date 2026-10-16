# Shared cash development portfolio

This module arbitrates existing JPR-035, MAR-089 and MAR-099 signals without
changing their parameters. It uses one $1,000 ledger per latency, a 100-share
fixed-limit first order per market, earliest signal then fixed priority on ties.
Every component validates its own feature availability; a missing mean feature
cannot block an otherwise eligible JPR signal. Failed first orders never retry.

Run on already consumed development data only:

```
python joint_portfolio_research.py ENDPOINT_JSONL_GZ BINANCE_PRICE_JSONL_GZ SCORES_JSONL_GZ OUT_DIR
python joint_portfolio_analysis.py OUT_DIR PRIOR_JPR_FULL_PLUS1C_ATTEMPTS PRIOR_MAR_FULL_PLUS1C_ATTEMPTS
python -m unittest test_joint_portfolio_research test_joint_probability_research test_mean_regime_research
```

Registration is written before replay and includes rule selection, inputs,
code hashes and all 27 execution scenarios. The analysis reuses existing
independent-rule ledgers only for a deliberately invalid additive reference;
it verifies identical first-signal timestamps before comparison.

The execution engine now separates official outcome knowledge/realized PnL
from spendable payout. Delays of 0, 60 and 300 seconds are assumptions, not
measured redemption. Payouts beyond the observation horizon remain locked
receivables, not cash. Zero delay preserves the prior cash-release default.
The engine CLI uses keyword arguments to avoid positional configuration drift.

All three stress variants charge one extra cent per filled share: partial FAK
depth, half displayed depth, and full-or-zero sensitivity. Full-or-zero is not
actual FAK. Latencies of 500/600/1000 ms are total local decision-to-match time,
including venue waiting. Latency and release scenarios cannot be added.

This is a post-selection development portfolio, not independent OOS. One date
cannot support a day-cluster confidence interval. The optional market bootstrap
is only descriptive and ignores prior selection and intraday dependence.
Realized drawdown excludes mark-to-market losses. No maker queue, hedge leg,
production changes, orders or authenticated data downloads are implemented.
New formal confirmation requires a later untouched protocol; earlier freezes
are not inherited or modified. Private inputs and output ledgers stay outside Git.
