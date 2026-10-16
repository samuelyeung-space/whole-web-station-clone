# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 763 | 643 | 6 | -0.87¢ | 1 |
| 350 ms | 763 | 644 | 6 | -1.25¢ | 1 |
| 400 ms | 763 | 638 | 6 | -1.00¢ | 1 |
| 500 ms | 763 | 641 | 6 | -1.54¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 242/1,000 fills across 4 days; EV -0.67¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 4 candidates dropped because the CLOB socket closed between the quote and the order (24 disconnects in the recording; test C counts its judged lag only)
