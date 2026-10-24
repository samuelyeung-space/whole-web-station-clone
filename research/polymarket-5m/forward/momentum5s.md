# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 2,751 | 2,279 | 14 | -0.37¢ | 1 |
| 350 ms | 2,751 | 2,277 | 14 | -0.91¢ | 1 |
| 400 ms | 2,751 | 2,287 | 14 | -0.76¢ | 1 |
| 500 ms | 2,751 | 2,292 | 14 | -0.90¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 1,000/1,000 fills across 8 days; EV -0.60¢, one-sided 99% day lower bound -4.67¢, exact p 1.
Status: `lower_bound_not_positive`.

## Recording notes

- btc _temp: 4 candidates dropped because the CLOB socket closed between the quote and the order (37 disconnects in the recording; test C counts its judged lag only)
