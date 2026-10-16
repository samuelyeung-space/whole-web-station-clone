# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 808 | 673 | 6 | -0.58¢ | 1 |
| 350 ms | 808 | 674 | 6 | -0.93¢ | 1 |
| 400 ms | 808 | 668 | 6 | -0.69¢ | 1 |
| 500 ms | 808 | 670 | 6 | -1.23¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 272/1,000 fills across 4 days; EV +0.03¢, one-sided 99% day lower bound +nan¢, exact p 0.529.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 56 candidates dropped because the CLOB socket closed between the quote and the order (207 disconnects in the recording; test C counts its judged lag only)
