# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 1,744 | 1,430 | 10 | -0.40¢ | 1 |
| 350 ms | 1,744 | 1,433 | 10 | -1.10¢ | 1 |
| 400 ms | 1,744 | 1,431 | 10 | -0.93¢ | 1 |
| 500 ms | 1,744 | 1,434 | 10 | -1.00¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 1,000/1,000 fills across 8 days; EV -0.60¢, one-sided 99% day lower bound -4.67¢, exact p 1.
Status: `lower_bound_not_positive`.

## Recording notes

- btc _temp: 28 candidates dropped because the CLOB socket closed between the quote and the order (128 disconnects in the recording; test C counts its judged lag only)
