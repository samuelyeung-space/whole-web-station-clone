# ECO input attrition audit

Read-only companion to the existing ECO-v1 generator. This is not a new strategy,
backtest, tuning operation, or permission to change an old freeze.

Run against the registered, already-consumed development derivatives:

```sh
python eco_input_audit.py --registration /path/to/eco/features/registration.json --out /path/to/new-audit.json
python -m unittest test_eco_input_audit -v
```

Every input is SHA256-checked before scanning. The audit retains all decisions,
including missing scores/quotes and failed warmup. It counts overlapping field
reasons and mutually exclusive missingness patterns separately. Per-market state
resets at each registered input, invalid source row, non-1000ms gap, and (only
for variance-normalized state) the 60-second variance-function join. UTC date
reporting does not reset a market. Outcome values are never read.

The independently predicted readiness is compared with all five generated
fields and their decision-time availability for every horizon/normalization.
Any mismatch fails the audit instead of silently changing an economic result.
Run-length histograms distinguish sparse observations from a clock-grid bug.
No missing values are imputed, no model is trained, and no rule gate is relaxed.

Missing saved scores do not prove absent raw recordings. Existing upstream JPR
score generation requires native causal features, healthy fresh dual-token
books, valid fees and historical training eligibility. This tool locates the
stored-input bottleneck; it does not assert the unobserved raw cause.

Keep the audit outputs private. Only this generic diagnostic and synthetic unit
tests belong in Git. All new/old economic comparisons remain $1,000 initial
cash, target 100 shares, partial FAK, separate 500/600/1000ms scenarios. No PnL,
formal OOS result, candidate count or independent mechanism is added here.
