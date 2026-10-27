# Frozen five-second momentum forward test

Signal frozen at `2026-10-01T06:20:50Z` from zoo100 rule #27; executable protocol frozen at `2026-10-03T15:00:57Z`. Paper only; no orders are submitted.

| Local signal-to-match lag | Signals | Five-share fills | Days | Net EV/share | Exact fair-price p |
|---:|---:|---:|---:|---:|---:|
| 300 ms | 3,472 | 2,889 | 17 | +0.60¢ | 0.2142 |
| 350 ms | 3,472 | 2,887 | 17 | +0.15¢ | 0.4307 |
| 400 ms | 3,472 | 2,903 | 17 | +0.31¢ | 0.3445 |
| 500 ms | 3,472 | 2,912 | 17 | +0.09¢ | 0.4605 |

Primary 400 ms fresh stopping sample (markets after the executable-protocol freeze): 1,000/1,000 fills across 8 days; EV -0.60¢, one-sided 99% day lower bound -4.67¢, exact p 1.
Status: `lower_bound_not_positive`.
