# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 3,203 | 2,661 | 16 | +0.77¢ | 0.1623 |
| 350 ms | 3,203 | 2,657 | 16 | +0.27¢ | 0.3746 |
| 400 ms | 3,203 | 2,674 | 16 | +0.44¢ | 0.2932 |
| 500 ms | 3,203 | 2,682 | 16 | +0.28¢ | 0.3675 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 1,000/1,000 fills across 8 days; EV -0.60¢, one-sided 99% day lower bound -4.67¢, exact p 1.
Status: `lower_bound_not_positive`.

## Recording notes

- btc _temp: 8 candidates dropped because the CLOB socket closed between the quote and the order (43 disconnects in the recording; test C counts its judged lag only)
