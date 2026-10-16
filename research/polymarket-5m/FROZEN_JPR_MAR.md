# Frozen JPR / MAR replay adapters

The 96 JPR and 144 MAR rules are existing development candidates. Their pure
standard-library plugins copy the original catalog and signal function text
exactly; `PROVENANCE` and `test_frozen_joint_strategies.py` bind the source and
function hashes. Wrapping the rules does not register new independent mechanisms.

`replay_saved_scores.py` joins saved JPR predictions to their exact registered
endpoint tapes. It preserves decisions without predictions. Oct7 is model warmup;
the saved scores cover portions of Oct8 and Oct9. The old saved models must not be
applied retrospectively to Oct1–6. The model audit requires labels known before
the relevant UTC day; a chronological prediction on consumed development is not
untouched OOS.

`replay_mar_saved_features.py` uses the unchanged original
`mean_regime_research.enrich`, actual Binance local-receipt trade prices, and those
same scores. Each input price gzip is extracted from the exact BTC trade member
of a verified historical recording. Price direction is never substituted for
missing aggressor direction. Its output also contains all JPR scores, so the
resulting final cache can serve both plugin families.

Before admitting saved features, `replay_saved_features_admission.py` checks the
original input hashes, development split, complete JSON/gzip, duplicate keys,
finite score values and probability ranges, every model/day training cutoff,
feature collisions and availability times. MAR additionally checks the
`imbalance_up` to `imbalance` alias source. Missing predictions remain missing.
Actual historical inputs have a separate admission audit; synthetic tests are
engineering evidence only.

Use the normal replay entrypoint after importing the enriched tapes:

```sh
python replay_lab.py catalog --strategy 'frozen_jpr_strategy:*' --strategy 'frozen_mar_strategy:*'
python replay_lab.py run --cache /absolute/path/to/cache --out /absolute/path/to/results --strategy 'frozen_jpr_strategy:*' --strategy 'frozen_mar_strategy:*' --shares 5 --latencies-ms 500,600,1000 --stresses base
```

Cold feature preparation and canonical tape ingestion are distinct from running
new strategies against the cached tape. Measure both; do not promise a fixed
runtime independent of tape size, candidate count, or execution simulation.

JPR's 5/15 setting is an EMA time constant, not a five/fifteen-second holding
period. Both families currently hold taker entries through settlement. MAR's
15/60/120 price-mean windows, with/without reference-slope gates and with/without
smoothness gates are retained exactly, including feature requirements for gates
that are disabled. Its reference slope is a trailing 60-second BTC-price mean,
not the official settlement TWAP. Maker queue calibration, dynamic hedging and
scale-in execution are not supplied by these plugins.

Report all rule attempts, failed/partial orders, fees, unresolved positions,
calendar coverage and the independent $1,000 cash ledgers. Different candidates
and latency scenarios cannot be summed. `replay_execution_overlap.py` detects
identical economic ledgers and reports overlap; all tried parameter variants
remain in the multiple-selection denominator. Six separated recordings totaling
about 27 hours across Oct7–9 are not seven complete days. Full-window PnL and OOS
results remain null when the corresponding evidence is incomplete.

`provenance/replay_saved_scores_v1.py` and
`provenance/replay_mar_saved_features_v1.py` retain the exact executed adapter
bytes for the first unified replay. They are provenance snapshots, not active
strategy plugins or new candidate families.
