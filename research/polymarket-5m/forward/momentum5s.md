# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 1,699 | 1,393 | 10 | -0.31¢ | 1 |
| 350 ms | 1,699 | 1,396 | 10 | -1.02¢ | 1 |
| 400 ms | 1,699 | 1,394 | 10 | -0.87¢ | 1 |
| 500 ms | 1,699 | 1,397 | 10 | -0.94¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 998/1,000 fills across 8 days; EV -0.74¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 28 candidates dropped because the CLOB socket closed between the quote and the order (100 disconnects in the recording; test C counts its judged lag only)
