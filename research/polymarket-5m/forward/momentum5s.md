# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 1,519 | 1,253 | 9 | -0.27¢ | 1 |
| 350 ms | 1,519 | 1,253 | 9 | -1.02¢ | 1 |
| 400 ms | 1,519 | 1,252 | 9 | -0.91¢ | 1 |
| 500 ms | 1,519 | 1,256 | 9 | -0.98¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 856/1,000 fills across 7 days; EV -0.78¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 37 candidates dropped because the CLOB socket closed between the quote and the order (138 disconnects in the recording; test C counts its judged lag only)
