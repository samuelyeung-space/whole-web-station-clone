# Reusable replay checkpoint and incremental forward evidence

2026-10-16. Research only; no orders, deployment, scoring-rule changes or new training.

## Cache recovery

`restore_replay_checkpoint.py` restores the data-only embedded checkpoint in the
existing research state. This complements `cached_strategy_replay.py` and the
historical adapters; it does not create another execution engine.

```sh
python3 restore_replay_checkpoint.py --state Polymarket_BTC5m_Research_State.json --key oct4_spot_development_20261016 --out replay_cache/oct4
python3 -m unittest test_restore_replay_checkpoint -v
```

Checks outer ZIP SHA256/size, exact member list, embedded manifest, every member
SHA256/size, unsafe paths, symlinks and pre-existing cache corruption. No existing
cache is overwritten. Repeat calls verify and reuse identical files. Stored code
is never executed. Restore the research code separately from the checkpoint's Git
head. Inputs remain development; restoring is not a new backtest or a new trial.

Acceptance: six synthetic unit tests passed. The real v89 checkpoint restored all
16 data members into a new workspace directory; a second invocation verified
reuse. Checkpoint SHA256:
`e34071afe650238583caaca05aafaa50c1d71d257d29bd7b8f4356951ed69e24`.
The normalized input and all 6480 scenario rows and attempt ledgers survive without
redownloading or reconverting the 522 MB original recording.

## New upstream evidence (not a locally rerun backtest)

Source commit `f19592738bd597b0c50142bcf628f0dd64270323` publishes run
`38838476036` (#83). The workflow finished with failure, but its report and retained
artifact were published. A workflow failure is not automatically a zero-PnL
strategy result, nor does a published report establish all execution gates.

Frozen five-second momentum cumulative report:

| Total lag | Signals | Five-share simulated fills | Reported days | Net cents/share |
|---|---:|---:|---:|---:|
| 300ms | 763 | 643 | 6 | -0.87 |
| 350ms | 763 | 644 | 6 | -1.25 |
| 400ms | 763 | 638 | 6 | -1.00 |
| 500ms | 763 | 641 | 6 | -1.54 |

These are cumulative figures, not 641 newly added fills, and the days are not a
claim of continuous 24h coverage. The registered 400ms post-freeze stopping subset
has 242/1000 fills across four days, -0.67 cents/share, exact p=1; the day lower
bound is undefined, not zero. Status remains insufficient_fills. Different clock
conventions and earlier consumed subsets must not be spliced into this sample.

Theta30 has 419 post-freeze markets, 28 orders and 17 persistent-depth fills across
four days, -5.65 cents/share, exact p=0.9219; also insufficient_fills. No new
qualified strategy is established. These reported results are not private fills
or fixed-100 evidence and do not validate the newly built mine500 normalization.

Raw artifact `12037537803` was published 2026-10-16T09:39:48Z, size 671803035 bytes,
SHA256 `eb03d5a3a9e475fc612de4441219a41a38c0ae98f2f11f438fd822a087ad9e8c`,
expires 2026-11-15T09:39:27Z. Its size exceeds the known 512 MiB connected download
limit; it was not downloaded. This is published-but-not-accessed, not nonexistent.

## Scope correction and next work

The Oct4 mine500 batch has not completed shared-cash/fixed-100 evaluation. However,
the project already has an Oct9 JPR/MAR shared-$1000 fixed-100 **development**
simulation in PDF v61: 29 opportunities, 15/15/12 full-or-zero simulated fills and
net +$429.92/+$417.87/+$449.53 at 500/600/1000ms including an extra cent/share.
Do not describe shared-cash modeling as wholly absent from the project, and do not
transfer that small selected sample's result to Oct4 or treat it as qualification.
The new checkpoint restoration does not add seven-day coverage or profits.

Next: batch-admit further consumed dates using existing adapters, preserve
per-date missing slots, integrate existing frozen JPR/MAR plugins and retain
original Oct10/11 confirmation boundaries. No new parameter selection here.

Sources: source branch `forward/momentum5s.md`, `forward/theta30.md` at the above
commit; original research state v89; report PDF v61. Package v30/PDF v61 unchanged.
