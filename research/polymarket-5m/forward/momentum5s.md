# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 1,094 | 908 | 8 | -0.73¢ | 1 |
| 350 ms | 1,094 | 910 | 8 | -1.45¢ | 1 |
| 400 ms | 1,094 | 909 | 8 | -1.19¢ | 1 |
| 500 ms | 1,094 | 913 | 8 | -1.37¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 513/1,000 fills across 6 days; EV -1.19¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 4 candidates dropped because the CLOB socket closed between the quote and the order (56 disconnects in the recording; test C counts its judged lag only)
