# Saved score upstream-admission audit

This extends the ECO attrition diagnostic upstream. It does not add a candidate,
fit a probability model, relax a rule, execute an order or calculate PnL.

```sh
python score_prerequisite_audit.py \
  --registration /path/to/mlq01/features/registration.json \
  --input-root /path/to/mlq01/features \
  --upstream joint_probability_research.py \
  --out /path/to/new-score-prerequisites.json
python -m unittest test_score_prerequisite_audit test_eco_input_audit -v
```

The source feature function is pinned to SHA256
`02f155469f7387dbe8dfb947f16e34f3b03414bf16aa455d5d2c23dc95a5aa09`.
Only its unchanged `finite` and `features` functions execute, with the registered
development stream and all outcome events removed. No sklearn import or model
training occurs. The independently reconstructed eligible market/receipt key
set must equal the original feature function's full output set.

Reports retain every decision, split upstream losses into mutually exclusive
first-failure categories, and separately expose overlapping missing features,
empty books, health, fees and native token quote-age reasons. The 250ms age is
the existing **input freshness** gate, not crypto venue waiting time. Do not
confuse this audit with changing the 150ms venue-hold baseline or total
500/600/1000ms execution stress.

The upstream derivative state can survive a single missing second if the next
eligible row arrives within two seconds; longer gaps initialize again. This
differs from ECO's own strict consecutive-grid warmup and must not be conflated.
UTC report boundaries do not reset either state. Input hashes are checked
before any scan. Primary categories partition the full decision denominator.

A vector without a saved score is not automatically a projection bug: the
first available development date has no earlier-date training sample, and
training admission is separate. Conversely, existing scores on rows that no
longer meet reconstructed prerequisites are explicit anomalies to inspect,
never an excuse to fill missing observations. Quote gaps are not evidence that
the raw AWS recorder stopped. Keep all audit result data private and preserve
all historical freezes, screening denominators and formal OOS null results.
