# 40004031507 published increment and legacy execution chronology
As of2026-10-23 23:59UTC. Development audit only. No orders, workflow dispatch, deployment or production edits.

The source branch advanced from28235ab296730545eaec8dc73552665ec7eed8b7 to **6f498b07f74c17c15f55a44ebb47f4af6ba6cd67**, one append-only result commit. The 18:32–22:32UTC native recording was already consumed in NATIVE400040_20261023.md; this audit processes only the newly published rows and code provenance.

## Newly published momentum rows

The entire old8,628-row momentum CSV is an exact byte prefix of the new8,808-row CSV. The180 new rows represent **45 signals across four correlated legacy lags**, not180 independent signals or strategy attempts. Git blobs:
- old6c51760ab2d19cb6d7d081160740935e0edca401
- new010119ace2f50b8f8cc25f0631b823ec44ce5358

| Legacy lag | Signals | Five-share proxy fills | Published proxy PnL USD | EV/filled share |
|---|---:|---:|---:|---:|
|300ms|45|38|-15.176125|-7.9874c|
|350ms|45|38|-15.946025|-8.3926c|
|400ms|45|39|-14.864730|-7.6229c|
|500ms|45|39|**-16.888780**|-8.6609c|

Each total equals five times the sum of reported per-share PnL. The CSV's `size` is displayed top-level capacity, **not actual order quantity**; it must not multiply returns. At500ms, six nonfills remain:3book_unavailable,1depth and2outside_frozen_price_band. No partial FAK or independentUSD1000cash ledger is established. 600/1000ms are absent, not zero.

All filled-row fees match the rate0.07/exponent1 feeSchedule in the market snapshot available before that signal; every reported per-share PnL matches the published win flag, quote and fee. This is arithmetic reconciliation, not venue-invoice or execution validation.

Captured native Gamma snapshots independently corroborate43of45signal markets, with no disagreement. The tail markets btc-updown-5m-1792794300 and1792794600 are final winners only in the upstream publication at this point; the saved native snapshots do not corroborate them. Both have reported fills. **Complete independently labeled PnL remains null**. Their possible win/loss payouts imply the500ms proxy bound[-26.888780,-16.888780]USD; even both winning cannot make this block positive. This bound holds only at the published proxy prices and quantities.

The newly published native paper ledger contains exactly the already reconciled21native rows, with identical decision identities, sides, win flags, shares, fees, costs and PnL. **Zero new independent native paper records** are counted. No prior short grid or financial replay was rerun.

## Three reproduced legacy execution counterexamples

The actual pinned `latency.py` Git blob is882b660db00090be28f1dc5d8e22e5653f79c63d; itsSHA256 is a5d8f525672009470b192f6456c103541e4278faf4ee28f9c067800d03fb0971. `momentum5s.py` blob a9dd8f97b25584d560c4907cb674724547dc57bf uses a Binance receipt-time signal grid, but its book implementation uses a different clock.

1. **Unreceived quote:** a CLOB row source-stamped100.0, locally received101.0, is selected by Book.at for a100.5local match. load_books maps source_ts to the index and preserves receive_ts separately; Book.at does not enforce receive<=match.
2. **Future disconnect filtering:** adding a socket close at100.8changes across_close(decision100.0,match100.5) fromFalse toTrue when the next quote arrives101.0. A post-match close can therefore reject an earlier simulated order.
3. **Synthetic complementary ask:** Down ask0.60and depth100are inferred from an Up bid0.40, without any direct Down book. This cannot establish executable Down capacity.

These are **three synthetic code witnesses**, not an estimate that three or any particular count of the180real rows used those unsafe paths. No new economic backtest, actual fill or profit evidence results from these tests. Do not simplify the current mixed clock to either fully source-time or fully local-receipt execution.

The legacy execute path also prices at the later quote without a persisted immutable decision-time limit and does not replay partial FAK. These results cannot enter the strict100shares ranking or validate the fixed local500/600/1000ms strategies. No legacy freeze or strategy code was changed. Two new read-only audit programs are reproducible against the above pinned source and published append.

## Strict artifact remains blocked

GitHub job120689991571 reports the `Require strict BTC replay artifact` step **failed**. Later family36analysis is still running; the converted archive publishing steps remain pending. At the final check only the original source index/four parts exist. They were fully verified and privately preserved previously; they are not downloaded again.

The running job's decoded log endpoint is currently unavailable. **The precise validation exception is not yet verified**; no dependency-drift or invalid-book cause is assumed from an older incident. Preserve this distinction when the final job logs and converted archive arrive.

Fresh official checks onOctober23 retain [crypto150ms fromSeptember4 14:00UTC](https://docs.polymarket.com/changelog/predictions) and [fee=C*feeRate*p*(1-p)](https://docs.polymarket.com/trading/fees). Per-market effective delay evidence and actual settlement/redemption availability are still required for strict execution. No extra150ms is added to an already measured total.

## Admission and continuation

New rules0; training0; strategy financial replays0; actual fills0; complete UTC days0. Day-cluster uncertainty and rank7/OOS results remain null. Confirmed independent large-edge mechanisms remain **0/10**.

The Oct24–31 common diagnostic reservation and every earlier freeze remain unchanged. The newer40034331093capture crosses that reservation's start: retain the reserved interval for its existing frozen evaluation; do not consume it for tuning or select a new profitable window. After the current converted archive and completed logs become available, verify the real strict exception and inputs, preserve any rejected data and replay only already fixed rules under their registered evidence category.
