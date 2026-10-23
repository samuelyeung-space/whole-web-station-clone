# Maker cancellation receipt audit — 2026-10-23

A new offline research inlet, `maker_cancel_receipts.py`, retains the distinction between a cancellation request, a venue acknowledgment and a fill received later. It audits explicitly normalized private receipts for post-only GTC/GTD BUY intents. It generates no orders, simulates no matching, infers no queue position, changes no strategy and calculates no PnL.

## Existing mechanism boundary

The current branch already contains the public-book complementary queue-lag detector, maker fill-cell analysis and the strategy-agnostic paired-leg FAK inventory replay with acknowledgment reservations. Those do not supply a private resting-order cancellation receipt ledger. This inlet addresses that evidence gap and is not a replacement execution engine or a new independent alpha mechanism. Source story US-040 remains a closed single-sided-maker rejection; its selected cells are not reopened.

## Explicit input contract

This is a canonical research schema, **not a Polymarket API parser**. A capture header declares development role, capture ID, local clock domain and an externally substantiated per-order venue lifecycle sequence. Every subsequent row binds its original raw-message SHA256, local receipt sequence/time, order, market and token. Submission additionally binds requested quantity, fixed limit, recorded order hold, BUY side, GTC/GTD type and post-only intent.

Supported local observations are submit, place acknowledgment, cancellation request, cancellation rejection, successful terminal cancellation acknowledgment, and fill. Cancellation responses bind a prior cancellation ID. A successful cancellation acknowledgment must partition the original quantity into cumulative filled and canceled shares. Fill IDs deduplicate exact economic repeats; conflicting copies fail.

The venue sequence is a required attestation, not something this program can authenticate or derive. Do not manufacture it by sorting source timestamps or assigning local receipt order. Without independently justified sequence mapping and original private messages, actual capture admission remains blocked. The existing GitHub public-book captures do not supply these private receipts.

## Receipt causality

A cancel request or rejection leaves the recorded order hold unresolved. If a successful cancel acknowledgment reports30 filled shares but their fill receipt has not arrived, the hold remains unresolved. A later local fill receipt with venue sequence preceding that acknowledgment is retained at its actual local receive time and reconciles those30 shares; earlier snapshots remain unchanged. A fill sequenced after terminal cancellation, an overfill, market/token identity drift, a fill above the original fixed limit, or regressed/missing local ordering fails.

The synthetic CLI example uses a100-share intent with a recorded$42 order hold. After an acknowledgment reporting30 filled/70 canceled shares, the hold is still$42; after the later receipt of the earlier30-share fill, its unreconciled-order-hold field becomes0. This field is **not spendable cash**: a full or partial fill can move capital into inventory. No fee, cost, $1000 cash ledger, settlement, redemption, capacity or real-fill qualification follows from this accounting output.

Unresolved tails remain `pending_private_receipts`. Quantity reconciliation alone does not certify capture completeness, actual venue provenance, maker fee eligibility, queue position or external account balances.

## Checks and persistence

Six new synthetic test methods pass, covering request-only and rejected cancellations, missing fill tails, a late earlier fill after cancel acknowledgment, duplicate/conflicting fills, fixed-limit/terminal-sequence contradictions, capture/token/clock binding, post-only resting BUY intent, and complete-fill accounting. The CLI example also runs successfully. These are6 engineering tests, zero market backtests and zero new candidate specifications; rank7 and formal OOS PnL remain null.

Code commits: module `09b323c693ab45ec468ee57824a30ec58b5bcad0`; tests `5ecbe7fd8e9ade32d3bf504a5ce0ff878d1a6ca8`.

SHA256:
- `maker_cancel_receipts.py`: `92b22a8dba17811814d5e281947a815877e3d0b9885612c1b73b776fef6c74f1`.
- `test_maker_cancel_receipts.py`: `41788f10da8e6c7c893dc6edf4624f0f10054b29a03462aedee7989f9a3740d0`.

```bash
python3 -m unittest test_maker_cancel_receipts -v
python3 maker_cancel_receipts.py --input canonical_private_receipts.jsonl --out receipt_audit.json
```

Any later financial integration must register its complete new code/input identities before use. Existing frozen protocols, the28 cash-guard tests and old financial results do not inherit this inlet. No private adapter deployment or live recorder change was made.

State181, package30 and PDF73 were restored after workspace maintenance removed prior local files. Source head remains `9b4d36ade9a4df643ab5080788ea0b410cbf251c`; source progress/prd and research head8547a51 were checked. At2026-10-23T18:31UTC, recording39970134236 remained in progress in “Save raw handoff independently of analysis,” with zero published artifacts. The already consumed399393 market replay and old short grids were not repeated. Continuous7UTC-day/2016-slot, isolated$1000/target100 ranking and strictly later untouched OOS remain pending/null; qualified independent mechanisms remain0/10.
