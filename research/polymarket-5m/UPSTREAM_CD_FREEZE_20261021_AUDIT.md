# Incremental C/D and source-race freeze audit — 2026-10-21 UTC

Scope: source commit `d0a1c8db8c0ef8eb51296be8e11668acc53ed6ef`, workflow
`39516477146`, and the unchanged source-race freeze escrow on research commit
`69a5414482e7c5796e463d624e9371468c18c9dd`.

## New upstream evidence

The workflow produced no downloadable artifact. It appended run `39471706733` to
the cumulative legacy C/D reports and committed only report/ledger files.

- C now has 3,571 cumulative 300 ms rows at **-0.77 cents/share** (`p=1`). Its
  preregistered first-1,500 verdict is unchanged at **-2.08 cents/share**
  (`p=1`): failed and terminal.
- D now has 2,383 cumulative rows at **+1.07 cents/share** (`p=0.1228`). Its
  preregistered first-1,200 verdict is unchanged at **-0.04 cents/share**
  (`p=1`): failed and terminal. The later positive cumulative average cannot
  replace the frozen stopping sample.
- In the newly appended run, C drops 4 and D drops 169 candidates after a CLOB
  disconnect. Both reports still use exchange-time alignment and require later
  health evidence. Therefore the cumulative rows remain legacy diagnostics, not
  receipt-causal rank7 evidence.

Neither test uses the common continuous seven-day/2,016-market, 100-share,
independent-$1,000 ranking ledger. No rank7 PnL is created.

## Freeze recovery audit

The ordinary source workflow failed the strict source-race lane on dependency
drift. The immutable escrowed freeze remains intact:

- escrow freeze SHA256:
  `29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63`;
- 18 declared dependencies checked;
- 16 resolve byte-for-byte from the current checkout;
- `eu_strict.py` and `poly_clob_rec.py` resolve byte-for-byte from the existing
  immutable snapshot;
- zero unresolved dependencies.

The escrow therefore makes the old evaluator reproducible without editing the old
freeze. It does **not** authorize rerunning already observed tape as new OOS. A
future formal run must invoke the frozen-dependency adapter and retain its audit
report; any changed strategy or execution code needs a separately registered,
strictly later untouched window.

`frozen_dependency_runner.py` audit-only mode now records all current and snapshot
hashes and every unresolved dependency in one machine-readable report. A blocked
audit returns exit status 2 without starting the evaluator. Execution mode still
fails closed before subprocess launch.

## Status

- New financial replay: 0
- New formal OOS result: none
- Rank7 PnL: `null`
- Qualified independent mechanisms: 0 / 10
- Frozen rules changed: no
- Orders / production changes: 0 / none

