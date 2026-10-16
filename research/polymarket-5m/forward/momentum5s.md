# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 718 | 599 | 6 | -0.56¢ | 1 |
| 350 ms | 718 | 600 | 6 | -0.96¢ | 1 |
| 400 ms | 718 | 595 | 6 | -0.76¢ | 1 |
| 500 ms | 718 | 599 | 6 | -1.29¢ | 1 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 199/1,000 fills across 4 days; EV +0.11¢, one-sided 99% day lower bound +nan¢, exact p 0.5255.
Status: `insufficient_fills`.
