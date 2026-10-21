# Forward checkpoint-order audit — run 39582692313

Observed at `2026-10-21T15:40:48Z`. This is a research-control audit only; it did not run an economic replay, place an order, or change the source workflow.

## Finding

The exact workflow used by the run (`head 6ff3027178a9c2fe1f0df750437a013dcc615a72`, blob `615a64e972039b908bef87f3964b8b9a8652812a`, content SHA-256 `f327807fcb7fc434cca12aea53bf4b214aa5acf00ee414d06a30965a663238a6`) has this order:

1. `Frozen five-second momentum paper test`
2. `Require strict BTC replay artifact`
3. `Persist pooled stopping state before nonessential artifact work`

At observation time, step 1 had completed successfully, step 2 was still running, and step 3 was pending. No converted artifact had been published.

The persistence condition is `${{ !cancelled() && needs.record.outputs.enabled == 'true' }}`. Under GitHub Actions status-function semantics it can run after an ordinary failure, but it does not run after cancellation. More importantly, a long or hung strict gate delays the first persistence point indefinitely. Thus the completed momentum mutation is not durable yet, and cancellation before step 3 loses that run's uncommitted append-only state.

## Required control repair

Add an append-only persistence checkpoint immediately after momentum and before strict replay. Keep a separate later checkpoint for strict replay outputs. This report does not apply that source-workflow change.

Rank7 PnL and formal OOS remain `null`; qualified distinct strategies remain `0/10`.
