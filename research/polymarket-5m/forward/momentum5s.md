# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 1,563 | 1,286 | 9 | -0.40¢ | 1 |
| 350 ms | 1,563 | 1,287 | 9 | -1.08¢ | 1 |
| 400 ms | 1,563 | 1,285 | 9 | -0.91¢ | 1 |
| 500 ms | 1,563 | 1,290 | 9 | -1.05¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 889/1,000 fills across 7 days; EV -0.79¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 24 candidates dropped because the CLOB socket closed between the quote and the order (112 disconnects in the recording; test C counts its judged lag only)
