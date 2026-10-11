# Family 31: Zero-fee dust-fill capacity audit

**Observed:** 2026-10-11T01:42:25.126615Z
**Verdict:** **INCOMPLETE**
**Evidence class:** public-row diagnostic only

## Scope and identification

The requested window starts 2026-10-03; 1 of 30 complete UTC days and 288 resolved BTC five-minute markets are currently included. The public taker-only feed contributed 318,806 rows. Each condition is read from its window open through one hour later, covering the five-minute trading window and delayed chain stamps.

A row is a theoretical zero-fee candidate only when `shares × 0.07 × price × (1-price) < 0.00001`. The inequality is strict. Public rows do not expose a unique trade id, parent order, match fragments, actual charged fee, or credited weighted volume. A larger public row can conceal several independently rounded match fragments. Transaction hashes and local occurrence counters therefore support overlap-safe row counting, not a complete upper bound on fee-free fragments or execution attribution.

## Capacity

| Metric | Directly visible public rows |
|---|---:|
| Theoretical zero-fee rows | 160 |
| Distinct transaction hashes | 160 |
| Markets with a candidate | 146 |
| Nominal weighted volume | 0.029476616 |
| Linear 30-day weighted volume at the observed daily rate | 0.884298480 |
| Fraction of Bronze (2,000 wV) | 0.044215% |
| Daily capacity multiple needed inside 30 days | 2,261.7× |
| Fills to Bronze at the observed candidate mix | 10,856,063 |
| Candidate notional | $1.58966 |
| Hold-to-settlement PnL before any operational cost | $-0.00014 |
| Largest candidate-wallet share | 81.88% |

The canonical 0.01-share fill at price 0.01 contributes 0.0002277 wV, so even that reference case needs 8,783,488 fills to reach Bronze. Bronze was not reached in the directly visible sample. At the observed rate Bronze is unreachable within a rolling 30-day window.

## Fixed controls

Candidate taker sides: BUY: 1, SELL: 159.
Candidate outcomes: Down: 81, Up: 79.
Candidate prices: 0.002: 1, 0.006: 1, 0.01: 12, 0.99: 111, 0.998: 1, 0.999: 34.
Candidate sizes: 0.01: 157, 0.02: 1, 0.05: 1, 0.11: 1.
The fixed ordinary-fee control contains 1,573 other 0.01-share public taker rows; their hold-to-settlement PnL is $0.2418. These controls are descriptive only because neither arm is a controllable parent-order sample.

## Decision

The sample cannot promote a strategy. Directly visible dust rows miss Bronze by orders of magnitude, but omitted match-fragment boundaries mean this public-row scan cannot support formal capacity rejection. No public row proves that an account can choose the partial-fill size or receive nominal weighted-volume credit. The frozen 30-day scan remains a concentration diagnostic; rejection additionally requires a fragment-complete chain or authenticated-account bound.

## Sources

- https://docs.polymarket.com/trading/fees
- https://docs.polymarket.com/programs/taker-rebates
- https://docs.polymarket.com/api-reference/feeds/list-trades
- https://docs.polymarket.com/api-reference/trade/get-trades
