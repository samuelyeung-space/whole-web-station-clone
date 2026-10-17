# Seven-day independent strategy ranking

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
python seven_day_ranking.py --protocol frozen_rank7.json --run COMPLETED_RUN --out rank7.json
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
