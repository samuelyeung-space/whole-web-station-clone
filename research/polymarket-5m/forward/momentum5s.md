# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 848 | 703 | 6 | -0.36¢ | 1 |
| 350 ms | 848 | 704 | 6 | -0.79¢ | 1 |
| 400 ms | 848 | 698 | 6 | -0.56¢ | 1 |
| 500 ms | 848 | 701 | 6 | -0.99¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 302/1,000 fills across 4 days; EV +0.27¢, one-sided 99% day lower bound +nan¢, exact p 0.4841.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 36 candidates dropped because the CLOB socket closed between the quote and the order (260 disconnects in the recording; test C counts its judged lag only)
