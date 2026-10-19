# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 1,432 | 1,184 | 9 | -0.37¢ | 1 |
| 350 ms | 1,432 | 1,184 | 9 | -1.09¢ | 1 |
| 400 ms | 1,432 | 1,182 | 9 | -0.98¢ | 1 |
| 500 ms | 1,432 | 1,187 | 9 | -1.03¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 786/1,000 fills across 7 days; EV -0.88¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 20 candidates dropped because the CLOB socket closed between the quote and the order (77 disconnects in the recording; test C counts its judged lag only)
