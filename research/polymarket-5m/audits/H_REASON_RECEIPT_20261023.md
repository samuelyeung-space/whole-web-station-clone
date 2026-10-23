# Current-H pre-compaction reason receipt — 2026-10-23

The registered original replay summary drops `_suppressed_unsent` counters. Its per-reason dictionary also overwrites counts when different sources share a reason. Sent/fill financial rows remain available, but a compacted old result cannot recover all source-specific unsent outcomes. The run399115 audit already recorded this limit; no past result is repaired by this change.

## Change and use

`h_reason_accounting.summarize_with_receipt` wraps the existing summary function. It returns the original result unchanged and exports a separate receipt containing original pre-compaction counters plus fully reconciled reason/source/latency buckets. The existing `audit_reason_buckets` performs the reconciliation. Both the fallback and explicit positional third-counter argument are preserved. Export fails if counts do not reconcile; missing old counters are never invented.

In a separately registered offline worker, after importing the hash-selected frozen closure:

```python
from functools import partial
from h_reason_accounting import summarize_with_receipt
original = run.summarize
run.summarize = partial(summarize_with_receipt, original, sink=write_receipt)
# Execute the already registered engine; label each independent arm in the sink.
```

This is a worker integration interface, not an automatically enabled CLI option. Register the wrapper, caller, sink and complete dependency hashes before any future replay. The old frozen launcher, engine, signal/execution rules, summaries and Oct10/11 or Oct12–19 protocols are unchanged. This interface cannot retroactively upgrade prior evidence or certify event-level provenance.

## Verification

Ten unique focused tests pass, including five new cases. The new checks compile only the two real summary functions from the pinned `h_replay_run.py` SHA256 `1d15596f4b63b78b2e843b112040e4842b961a41e158efcdbd2ebea1d891f2c4`; they do not replay market data. In a synthetic shared-reason example, 7 spot +11 Deribit suppressed signals produce18 reasons in the receipt, while the legacy summary's11 and every economic field remain unchanged. Other cases cover explicit-counter precedence, mutation isolation, missing/invalid buckets and no-signal output. No whole-engine parity or new profitability claim is made.

Final validation: `python3 -m unittest discover -s research/polymarket-5m -p 'test_h_reason*.py' -v` →10passed. There were three engineering test invocations while preserving positional compatibility; unique cases remain10, new cases5.

Code commits: ae27a8a7aba630c5495221daa6dfe8a15dae116a; 2bb1bc38527b4ccf869719114da86c701a4de007.

## New capture intake remains pending

GitHub run39939346652 published a native capture index and five opaque parts on2026-10-23 around14:29UTC. The index declares2,078,115,840 source bytes and source SHA256 `02a4311302488c916729f708fc13cc1a82abdf8f8456f43134ccf62be9e598a2`. Only part0 has been downloaded and independently verified at outer ZIP, inner ZIP and payload layers. The source archive as a whole is **not verified**; interval, markets, labels, flow integrity and PnL remain unknown/null. Four part downloads and a serial part1 retry returned MCP request timeouts; an earlier batch's individual outcomes were not fully retained. This is a transient access failure, not source corruption or zero profitability.

At14:41:11UTC, authoritative Actions artifacts still contained native parts/index only; no converted strict archive was published. Source strategy head remained4abdc4b7200fb9fabbc5dc573b55ff99e0c44c5e and priority progress/prd blobs remainedcf470e07/984b5669. Preserve the new artifact IDs/digests and retry only missing parts or a newly published converted artifact. The original capture parts expire around2026-10-24T14:29UTC. No AWS access or production/workflow change was attempted.

Official rules rechecked2026-10-23: [dated changelog](https://docs.polymarket.com/changelog/predictions) retains150ms crypto taker delay effective2026-09-04T14:00UTC and60s5m TWAP effective2026-08-14T00:00UTC; [fees](https://docs.polymarket.com/trading/fees) retains C×0.07×p×(1−p) for crypto. Actual market parameters and match-time effectivity still require evidence; measured total latency must not add venue hold twice.

No new financial replay, strategy candidate, formal OOS or rank7 row was produced. Rank7/OOS results remain null and confirmed independent mechanisms remain0/10. No trade, deployment, production change or outbound message occurred.
