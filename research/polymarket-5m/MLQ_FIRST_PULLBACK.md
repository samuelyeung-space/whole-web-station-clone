# MLQ-01: causal first pullback, paired development experiment

This is a researcher-defined BTC5m transfer hypothesis inspired by the previously registered Martin Luk research. It is not his native US-equity strategy, his trade history, or an estimate of his returns. All exact numeric rules below are ours. The original eight-hypothesis registry is preserved; this is its first narrow executable amendment.

## Difference from existing rules

M500 `pullback_5_60` / `pullback_15_60` and MAR `trend_pullback` already test generic trend and pullback. Do not count these as new independent mechanisms. MLQ tests one incremental question: does retest ordinal (first versus all retests) improve an otherwise identical signal? Four variants are registered: closed confirmation bar 1s or 5s, each with first-only and all-retests. Every attempt counts toward the search denominator.

## Causal features

Use only actual BTC receipts. A closed second ending at t contains receipts strictly before t. Last received price may be carried for at most 3000ms; this is a causal price observation, not an invented trade. No quantity/aggressor volume is created. A gap beyond that resets warmup and anchors. EMA decay seconds are 15/60; require120 consecutive valid closed seconds. Scale is trailing60 population log-return SD times current price times sqrt60, numerical floor price*1e-8. Slow EMA ten-second change divided by scale must exceed0.5 in trend direction; fast EMA must be on that side of slow EMA.

An anchor begins only on a close beyond the preceding60 closes and directional distance from fast EMA above1scale. Anchor is fixed until trend gate fails or300seconds pass. An excursion above1scale followed by a return inside absolute0.5scale increments the retest ordinal. A completed1s/5s bar moving in the trend direction consumes the confirmation once, irrespective of later q/cost rejection. First-only and all-retests use identical confirmations except for ordinal==1.

At decision time use the existing saved `book_5` model without retraining. Absent q remains blocked. Require q minus observed ask minus recorded fee>=0.02, remaining time60–240s. No official settlement TWAP feature is inferred. Execution uses unchanged audited replay engine, fixed-limit FAK, target100shares, initial$1000, total latency500/600/1000ms, official-settlement hold. Cash is continuous across tapes with no daily reset. Depth remains a simulated capacity upper bound. Structural-stop/timeout, scale-in, AVWAP and maker variants are not implemented by this amendment.

## Reproduction

1. Run `python -m unittest discover -s replay_work -p test_mlq_first_pullback.py`.
2. Generate features using `mlq_first_pullback_features.py --manifest <existing MAR feature manifest> --manifest <incremental manifest> --out <fresh output>`.
3. Import all six generated normalized tapes with `replay_lab.py pipeline`, strategy `mlq_first_pullback:*`, shares100, cash1000, latencies500,600,1000.
4. Inspect per-attempt/cash ledgers and completion output hashes. Do not mix these results with frozen Oct10/11 OOS.

The supplied development evaluation reuses consumed Oct7–9 recordings and original earlier-day model scores. Results have selection bias from historical model development and only two dates with scores; positive results are not OOS confirmation.
