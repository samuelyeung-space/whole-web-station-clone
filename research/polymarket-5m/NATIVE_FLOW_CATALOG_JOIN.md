# Native flow to existing candidate rules: development admission

Date: 2026-10-16. Research only; no orders or recording deployment.

`native_flow_catalog_join.evaluate_decision` connects the existing native
Binance bridge to the **unchanged** mine500 candidate registry. This is the
next integration layer after native parsing, not a new alpha engine or a
profit replay. It enumerates every flow-dependent variant, retaining its ID,
template, rule fingerprint, missing-field reasons and rule output.

## Coverage of existing rules

| Input needs beyond tau | Templates | Parameter variants |
| --- | ---: | ---: |
| Native 15/60-second flow only | 8 | 80 |
| Flow plus causal BTC returns | 8 | 80 |
| Flow plus realized volatility | 1 | 10 |
| Flow plus official TWAP tail inputs | 1 | 10 |
| Total existing specifications | 18 | 180 |

These are existing candidates, not 180 new attempts or independent mechanisms.
With a synthetic fully covered flow tape and valid tau, only the first 80
variants have all necessary inputs. This does not imply that all 80 trigger:
tau gates and economic conditions still apply. The other 100 retain explicit
blockers. True direction/quantity/coverage are still unavailable in the old
price-only archive; none of these counts upgrades historical availability.

## Contract

Call `evaluate_decision(tape, decision_recv_ms=..., capture_clock_id=...,
auxiliary=..., split='development')`, where tape is a `NativeFlowTape` built
from retained raw packets and independent recorder receipt clocks.

Every auxiliary field (including tau) is a mapping with:

```python
{'value': 120.0, 'as_of_ms': 60000, 'available_ms': 60000,
 'capture_clock_id': 'recorder-domain-1', 'provenance': 'input manifest reference'}
```

The timestamps above are illustrative, not market observations. The feature
must be computed **for this decision** (`as_of_ms == decision_recv_ms`) from
inputs already available then. A previous tau or rolling return is not carried
forward. A timestamp/provenance declaration cannot prove correct computation:
upstream source hashes, receipt clocks, window logic and domain correspondence
still require independent evidence. The module expressly reports that clock
domain truth is not independently verified.

The caller cannot overwrite native flow fields. Missing quantity or aggressor
side is not replaced by unit size, price direction, zero or future values.
Existing coverage validation remains authoritative; a later coverage claim
cannot enable an earlier decision. No source-clock alignment is introduced.

## Output and boundaries

Each candidate is `blocked_features`, `outside_tau_window`,
`rule_not_triggered`, or `signal`. The output includes the native snapshot
digest and per-field admission audit. Signal rows are **not** orders, fills,
or market-level attempts. New candidates, backtests and orders are all zero;
PnL and formal OOS results are null. Non-development splits are rejected.

This layer does not fit a model, set a limit, simulate FAK, calculate fees,
allocate cash or infer settlement. A future replay must route admitted signals
through the established first-signal/fixed-limit/execution ledger and record
all failures. Frozen October protocols are not compatible with this new join
by implication; any formal adoption requires a new, later untouched protocol.

## Verification

Run from this directory:

```sh
python3 -m unittest test_native_flow_catalog_join test_binance_native_flow test_historical_flow_receipts -v
```

50 synthetic engineering tests pass (15 new join tests; 35 existing parser and
receipt-flow tests). Coverage includes ID/fingerprint preservation, exact
catalog signal parity, missing/prohibited fields, old/future feature stamps,
clock-domain mismatch, future receipt invariance, unknown native side, and
refusal of formal/OOS labels. No historical tape was replayed for profit.
