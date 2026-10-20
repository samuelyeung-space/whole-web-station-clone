# Seven-day independent strategy ranking

## 2026-10-20 input-admission hardening

Current schema is `rank7-protocol-v2`; the null template is still not a frozen
experiment. Supply `--cache` as well as the completed run. The protocol must
pin both admission modules in `admission_code_sha256`, in addition to the
original execution/input/strategy closure. Assessment schema is now v2.

The producer already counts the common registration/book/decision/outcome
market set in its daily presence summary. The gap was independent admission:
the old ranking gate could accept a hand-built internally hashed report with
2016 market definitions, 288 claimed daily presence slots and settled fills,
without checking the unused markets' actual books/decisions/official outcomes.
No real strategy had entered this rank; the demonstrated acceptance was a
synthetic accounting fixture, not a real-data defect or profitable replay.

`rank7_input_window.verify_window` now verifies the pinned cache bytes and
identity, checks the current dataset validator against its frozen code hash,
revalidates the receipt-ordered canonical input and its event digest, and
re-derives all 2016 fixed markets and all three required presence sets. It
compares actual market definitions and complete-day coverage with the report
and rechecks the cache after scanning. A missing result on even a market with
no strategy trade blocks ranking. Settlement tails remain allowed. It never
computes strategy signals, PnL, rankings or a new model.

This still does not authenticate a source's claims, establish queue/private
fills, or prove continuous feed health. A development split label is a caller
attestation, not proof that data was untouched/consumed. Do not run this
label-reading check against protected prospective data for candidate selection.

Missing cache, mismatched identities, missing events, actual recording gaps,
unsupported/protected splits, corrupted streams, and old v1 protocols remain
blocked with null results. Do not rewrite Oct10/11 or Oct12-19 freezes or
reinterpret them with v2. A changed schema/code closure requires a separately
registered strictly later untouched interval for formal use; none is registered
by this patch. The Oct19-26 design is not upgraded to a frozen experiment.

29 synthetic tests pass: 16 existing regression cases and 13 new admission
cases. Those are engineering fixtures, not 29 candidate attempts or backtests.
No real input, old financial grid, or prospective window is replayed for this
change. PnL/OOS/qualification remain absent.

Run tests: `python3 -m unittest test_seven_day_ranking -v`.

The user requires promising developed rules to complete a seven-day replay
before entering the main comparison. Every strategy and every alternative
scenario starts with its own $1,000 and requests 100 shares per order. Cash is
not shared between strategies, reset daily, topped up, or borrowed. A shared
portfolio is a separate experiment and never substitutes for this ranking.

1. Develop on consumed data, retain all trials, and identify a candidate.
2. Freeze the candidate/model/dependency identity, execution identity, common
   UTC window, BTC market universe, input hashes, and cash-release assumption.
   The template is deliberately null and is not an experiment registration.
3. Admit the complete seven consecutive UTC days: 288 aligned five-minute
   slots each day, 2,016 overall, with registered markets, books, decisions,
   and official outcome evidence. Declared time span and event presence do
   not certify continuous feed health. Keep unhealthy periods and execution
   failures; do not remove them after seeing outcomes.
4. Replay each rule independently with fixed first signals/limits, partial
   FAK and failures, native fees/constraints, cash reservations and the
   registered payout-spendability assumption. Use 500ms total local-decision
   to match for the headline; 600/1000ms and depth/cost stresses are separate.
5. Run `seven_day_ranking.py` against the frozen protocol and completed
   replay-lab output. Output hashes, full gzip streams, common window,
   candidate and execution identities must pass. Warmup and settlement tails
   may exist, but orders outside the declared test window cannot contribute.
6. Only admitted, complete, filled-strategy results enter the historical
   development ranking. Preserve missing/incomplete/no-signal/no-fill rows
   separately, with null versus actual zero distinguished. Do not sum
   variants, latency buckets, overlapping strategies or funding assumptions.

Example invocation after a real protocol and completed replay exist:

```bash
python seven_day_ranking.py --protocol frozen_rank7.json --run COMPLETED_RUN --cache PINNED_DEVELOPMENT_CACHE --out rank7.json
```

`replay_lab.py --require-full-days 7` is a useful preflight but cannot alone
prove that two result sets used the same explicit seven-day window. The
standalone ranking gate preserves the old engine and all old freezes.

A seven-day historical ranking remains development evidence. It does not
restore a repeatedly consumed sample to OOS, establish real fills/capacity, or
qualify one of the ten distinct executable mechanisms. Formal confirmation
requires a separately frozen later untouched interval and its original
statistical protocol. Never pick the best seven days after seeing PnL.

As of the October17 intake review, the connected September29-October9 views
have no complete recording day. Their 174.33 observed hours are fragments,
and JPR/MAR views overlap the underlying strict tape. Main seven-day ranking
therefore stays pending; no seven-day PnL is imputed. See the current private
research state for the candidate queue and data admission evidence.

