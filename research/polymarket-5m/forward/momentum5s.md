# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 3,250 | 2,701 | 16 | +0.75¢ | 0.1692 |
| 350 ms | 3,250 | 2,696 | 16 | +0.23¢ | 0.3926 |
| 400 ms | 3,250 | 2,713 | 16 | +0.40¢ | 0.31 |
| 500 ms | 3,250 | 2,723 | 16 | +0.26¢ | 0.3734 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 1,000/1,000 fills across 8 days; EV -0.60¢, one-sided 99% day lower bound -4.67¢, exact p 1.
Status: `lower_bound_not_positive`.

## Recording notes

- btc _temp: 16 candidates dropped because the CLOB socket closed between the quote and the order (101 disconnects in the recording; test C counts its judged lag only)
