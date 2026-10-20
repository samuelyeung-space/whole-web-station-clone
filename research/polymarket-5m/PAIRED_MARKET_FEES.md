# Historical market fees in paired-leg replay

The optional `market_fees=` argument extends the existing shared-cash/two-leg
engine, not strategy selection. It removes the global-rate assumption for this
engine only; other historical and joint-probability engines already have
market/quote-scoped rates. No second fee formula or strategy engine is created.

Supply exactly one `MarketFee` record for every replay market, with no missing,
extra or duplicate market. Each record has `market_id`, `fee_rate`,
`effective_from_ms`, `effective_until_ms`, `observed_ms`, `source_ref` and
`evidence_sha256`. The rate is a finite JSON number in [0,1]; clocks are exact
non-negative integers; the source reference must be nonblank and the evidence
identity a lowercase SHA256. Records and hashes are **caller-supplied identities,
not authenticated evidence**. Actual fee documents or captured endpoint responses
still need independent verification and input-hash binding.

The half-open effective interval must cover every order from its decision
through its match. The local observation must strictly precede decision; an
equal clock fails closed because ordering is not proved. Announced future
changes may be known in advance, but cannot apply before their effective time.
An epoch ending exactly at match fails closed. This version deliberately rejects
multiple fee epochs in one market rather than guessing how a fee change affects
an already-sent order. Do not drop the offending orders or select a profitable
fee epoch; retain the whole experiment as pending until supported.

The existing crypto formula `shares * fee_rate * p * (1-p)` and per-order
five-decimal Decimal half-up convention are unchanged. This is not a new audit
of official rules, an assurance that every historical market used this formula,
or permission to replace historical parameters with today's crypto rate.
Other fee formulas are unsupported. Use the order's market rate both for actual
partial/full fill debits and the send-time p(1-p) worst-case reservation.
Zero/failed orders incur no simulated fee. The ACK reservation and shared
snapshot-depth options compose with this mode.

Opt-in output is schema 9. The fee audit states that config.fee_rate was not
applied, lists the sorted records and annotates each send/arrival with its rate
and evidence identity. With `market_fees=None`, API output is unchanged, including
schema 7 defaults and schema 8 ACK stress. The CLI intentionally uses a separate
file to avoid silently treating inline fee metadata as applied:

```
python3 paired_leg_inventory.py intents.json --market-fees fees.json --output result.json
```

`fees.json` is an array of the record objects above. Inline `market_fees` in the
intents file now fails closed with guidance to use the flag.

## Verification and research status

```
python3 -B -m unittest test_paired_market_fees test_order_ack_reservations -v
```

Eighteen new standard-library tests cover independent rates, exact rounding,
reservation cash rejection, partial/zero FAK and full-or-zero failures,
decision/match interval boundaries, late/equal observations, strict types,
missing/extra/duplicate metadata, CLI routing, ACK/depth composition, permutation
invariance and 100 seeded ten-market cash-conservation paths. The 13 ACK tests
also pass. A separate 1,000-case complete-output comparison against parent
`c16638860869c55a3e5e55780fac093eb3ef6d51` passes for legacy API calls with and
without ACK stress. Small-cash synthetic fixtures are not ranking accounts.

No historical input, private receipt, strategy candidate, financial replay,
PnL, OOS observation, ranking admission or production change is added. Existing
fee evidence gaps remain; identifiers are not substitutes for missing evidence.
The old Oct10/11 freezes and Oct12-19 protocol are untouched. This extension is
not bound into a current rank7 protocol: formal adoption requires exact new
execution/input/scenario hashes and a strictly later untouched window. Every
single strategy/scenario must still independently use $1,000 and target 100
shares for the same complete seven UTC days; shared portfolios remain separate.
