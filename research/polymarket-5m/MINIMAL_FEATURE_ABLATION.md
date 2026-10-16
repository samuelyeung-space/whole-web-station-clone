# MFA-v1: minimal feature dependency ablation

New development experiment, not an edit of original JPR/ECO or a frozen holdout.
Eight rule variants are counted as additional research trials, not independent
economic mechanisms: time/trend inputs, fixed/expanded training admission, and
4%/8% fee-aware probability advantage. All use tau30..240 and official settlement.

The six time features and twelve trend features use the same component formulas
as old JPR. They require a receipt-causal fresh direct Up BBO for p/spread. Trend
also requires all four receipt-causal external returns. Neither feature set
needs Down depth, imbalance or the original recursive derivative state. The
model may therefore emit an estimate when trading is still impossible.

Fixed training uses only the exact original JPR vector-eligible row keys and
prior-date, locally-known official labels. Predictions at original scored rows
must match old stored time_5/trend_5 within1e-10. Expanded training uses a larger
prior-date sample from that model's own dependency contract. This isolates
expanded test admission from separately changing the fitted model. It is not
valid to attribute an expanded/refitted result to missing-field handling alone.

Both use original fixed HGB parameters, per-market balanced weights, at least
40 earlier-date training markets, and labels locally received strictly before
the test-date UTC cutoff. All Oct7-9 inputs are already-consumed development.
There is no first-day fit and no backward score filling from a later model.
Formal OOS remains null. A genuinely later protocol would be registered anew.

Execution uses the unmodified research engine: $1,000 initial cash per isolated
candidate/scenario, target100shares, receipt-to-match500/600/1000ms, fixed limits,
partial/failed FAK, contemporaneous market fees/ticks/minimum sizes, full cash
occupancy and official outcome release proxy. Native direct dual-token price
and health checks remain. `mfa_ask_up/mfa_ask_down/mfa_fee` are separately rebuilt
from decision-known fresh healthy native books; old saved features are not
overwritten. Half-depth and plus1cent/filled-share are separate stresses.
Displayed depth remains an execution upper bound, not proof of actual fills.

```sh
python minimal_feature_research.py --registration /path/to/registered/inputs.json \
  --input-root /path/to/consumed-inputs --output /path/to/new-mfa/features
python replay_lab.py pipeline --input /path/to/new-mfa/features/endpoint_0.jsonl.gz \
  /path/to/new-mfa/features/endpoint_1.jsonl.gz /path/to/new-mfa/features/endpoint_2.jsonl.gz \
  /path/to/new-mfa/features/endpoint_3.jsonl.gz /path/to/new-mfa/features/endpoint_4.jsonl.gz \
  /path/to/new-mfa/features/endpoint_5.jsonl.gz --input-type normalized \
  --cache-root /path/to/new-mfa/cache --out /path/to/new-mfa/results \
  --strategy 'minimal_feature_strategy:*' --shares 100 --cash 1000 \
  --latencies-ms 500,600,1000 --stresses base,half_depth,plus_1cent
python -m unittest test_minimal_feature_research -v
```

`run_minimal_feature_batch.py --root /path/to/new-mfa` registers and runs the
complete grid plus a single shared-$1000 control using all eight rules in
canonical catalog priority, with no performance-based component selection.
This is72isolated rows plus9shared rows, not81strategies. Its new batch protocol
is written before any replay result; first failed orders still consume markets.

Private tapes, predictions and ledgers must not be published in the public repo.
Never sum alternative training/threshold/latency results or reset the screening
denominator. This batch is selected after other October development results and
cannot provide independent confirmation or a daily/monthly return forecast.
