# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 889 | 734 | 7 | -0.97¢ | 1 |
| 350 ms | 889 | 735 | 7 | -1.37¢ | 1 |
| 400 ms | 889 | 729 | 7 | -1.16¢ | 1 |
| 500 ms | 889 | 732 | 7 | -1.58¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 333/1,000 fills across 5 days; EV -1.11¢, one-sided 99% day lower bound +nan¢, exact p 1.
Status: `insufficient_fills`.

## Recording notes

- btc _temp: 20 candidates dropped because the CLOB socket closed between the quote and the order (95 disconnects in the recording; test C counts its judged lag only)
