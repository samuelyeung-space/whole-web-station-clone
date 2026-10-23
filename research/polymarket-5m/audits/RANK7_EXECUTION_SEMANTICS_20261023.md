# Rank7 cash-to-execution semantic checks — 2026-10-23

The independent cash guard now rejects internally balanced ledgers whose execution fields contradict the stated fixed-limit fill or binary payout. This is a prospective admission repair, with no signal, matching-engine, strategy, production, old frozen protocol or historical financial result changes.

Before the repair, five synthetic mutations of the actual replay engine's test output were accepted: flipping the winner; flipping the bought side; lowering the fixed decision limit below the unchanged fill price; labeling a full target fill as partial while also updating summary counts; and marking an immediate payout unreleased while cash was already credited. The arithmetic and cash transitions were deliberately left consistent. These are engineered counterexamples, not evidence that a published market ledger contained those errors or that any historical strategy profit was inflated.

The guard now requires:

- Every submitted order has an explicit finite fixed limit strictly between zero and one.
- Positive filled quantity has a full/partial fill status consistent with the 100-share target, and zero filled quantity cannot carry a filled status.
- Fill VWAP is a valid binary price and cannot exceed the decision limit.
- Explicit Up/Down side and winner imply the stated payout: shares for a matching side, zero otherwise.
- Immediate or zero payouts cannot be marked locked after the recorded cash transition credits them.

Price/quantity classification uses the pinned replay engine's 1e-9 tolerance separately from the looser cash reconciliation tolerance. Existing valid partial FAK fills, including a near-full partial fill within 1e-7 of the target, remain valid. Full/partial/failed orders, fees, totals, independent $1000 balances, collateral, delayed payout release, midnight continuity and summary reconciliation remain covered by prior tests.

Five new unique test methods plus the 23 existing cash/admission tests pass: **28/28**. The five original pre-repair probes are accepted by the old guard and all blocked after repair. The first combined check found an older empty-cash negative fixture lacked the newly required fixed-limit/side/winner metadata; the fixture was completed so it still fails specifically for missing mandatory cash transitions. No price-data replay, synthetic PnL promotion, new candidate or OOS/rank7 result is claimed.

Code commits: guard `5539466acca17ae45e6e572459d6cd4b6f5fcec1`; new unit cases `9b171dabf2acfc8ccea5b1ce04d210e8be006321`; integration fixture `671c245832aefc6850b7bdd25787500bc0bb1769`.

SHA256:
- `rank7_cash_integrity.py`: `2e7f855fe7dca04167a120430e5e15505ce110f99e8d6170e6083c4198549dd4` (previous `d441b3591d4bde1da8d68da8fb26f85271ee35c151c01c8533ae90974bf1006e`).
- `test_rank7_cash_integrity.py`: `a62ca294f8ea8fd341b650897d2b6d769cc99f8c97bd33e36c435cfc337ccf60`.
- `test_rank7_cash_admission.py`: `bf5b4c93647edcea475e2b4e58f470484e692d73d725c62a08068972872575f7`.
- Unchanged `rank7_cash_admission.py`: `da38ea60d7bd949a8d5483dae9c6e980a3e19b8f3b0cb4b49c2dd0174c4639df`.
- Unchanged `replay_engine.py`: `5a293f7a773d46335d2ddf4fe342f400863b616ddd4f9027ae38444d15289785`.
- Unchanged `seven_day_ranking.py`: `0fb6bc510295d2ead734f625263ec21e8bdd46f8c4b756196fe269da78c1c57c`.

Any future use must durably register this exact new guard hash, both cash admission dependencies, the nested rank7 protocol and the full execution/input identities before replay. Existing Oct10/11, Oct12–19 and Oct19–26 freezes cannot inherit the new checks. This remains internal consistency verification: the recorded winner still needs an independent official-outcome join, and the checker cannot prove real execution, unchanged order intent, venue queue position, spendable redemption receipts or untouched OOS.

State 180/package 30/PDF 73 were restored. Source head remains `9b4d36ade9a4df643ab5080788ea0b410cbf251c`; source ralph/progress.txt and ralph/prd.json were read. Recording39970134236 and shadow39980161751 are in progress with no published artifacts at this check. No new completed input is available;399393's four passes/12 alternative cells/584 rows remain already consumed and were not rerun. Continuous complete seven-day/2016-slot, isolated $1000/target100-share ranking and strictly later untouched OOS remain pending/null. Qualified independent mechanisms remain 0/10.

Reproduce engineering checks from the research directory:

```bash
python3 -m unittest test_rank7_cash_integrity test_rank7_cash_admission -v
```
