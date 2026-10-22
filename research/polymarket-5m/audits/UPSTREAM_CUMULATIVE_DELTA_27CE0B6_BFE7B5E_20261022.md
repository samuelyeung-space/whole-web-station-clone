# Upstream cumulative-report delta: 27ce0b6 to bfe7b5e

As of 2026-10-22T01:20:27Z, source workflow run `39692744707` completed successfully and committed cumulative reports at `bfe7b5e9680ad39e9b47997001a793c7f4980357`. The workflow published no artifacts. This audit consumes only the report delta after source revision `27ce0b6f7c3ddcc27676c5134c934c7e5adb2dab`.

This is research-only evidence. No order was submitted and no production position, risk limit, execution setting, or frozen strategy rule was changed.

## New cumulative display

The appended recording segment is `39650865752`, already audited independently as about 4.003 hours with 50 settled BTC5m markets and zero complete UTC days. Relative to the preceding source report:

- C, 300ms: 3,820 trades, -0.71 cents/share, exact p=1.0000; +46 trades.
- D, 300ms: 2,568 trades, +1.65 cents/share, exact p=0.0302; +38 trades. Its frozen first-1,200 verdict remains failed.
- F, 300ms: 2,441 trades, +4.76 cents/share; +32 trades. The historical frozen verdict is unchanged.
- G, 300ms: 1,210 trades, +12.09 cents/share; +26 trades. The historical frozen verdict is unchanged.
- I, 400ms: 1,246 markets and 1,896 trades, +8.86 cents/share; +26 markets and +56 trades. The historical first-1,000-market verdict is unchanged.
- J, 500ms: 1,971 sends across 1,237 markets, +1.77 cents/ordered share, p=0.0064; +45 sends and +26 markets. At 600ms the cumulative display is +1.39 cents/ordered share, p=0.0231. The historical first-1,000-market verdict is unchanged.

These are correlated cumulative development displays. They are not fixed-100-share, $1,000 single-strategy Rank7 ledgers and cannot be added together.

## Immutability failure

The commit is not append-only. It rewrites six previously published fields for historical recording run `38909708508`:

- F candidates dropped: 1,694 to 1,720.
- G candidates dropped: 1,697 to 1,728.
- I candidates dropped: 3,554 to 3,582.
- Recorder urgent moves: 545 to 546.
- Recorder plus-1000ms percentage: 79% to 78%.
- Recorder first-move summary: 255 / 1703 to 251 / 1676.

These are reversals of the immediately preceding report values and demonstrate that the cumulative tables remain mutable. The source branch also replaces the compressed cumulative trade table rather than publishing an immutable per-run artifact. The entire report delta therefore fails closed for Rank7 and formal OOS admission.

## Admission decision

- Complete recorded UTC days added: 0.
- Rank7 PnL: `null`.
- Formal OOS result: `null`.
- Confirmed qualified independent mechanisms: 0/10.
- Recording/report workflow artifacts: none.

The cumulative figures remain useful only as development diagnostics. Formal admission still requires an immutable common seven-complete-UTC-day input set, 2016 slots, official outcomes, fixed costs and execution code, and the independent $1,000/100-share ledgers.
