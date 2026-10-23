# Frozen source-race reason receipt worker — 2026-10-23

The previous change supplied a lossless summary-export interface. This change connects that interface to a separate runnable offline entry point, `run_source_race_reason_audit.py`. It uses the existing frozen dependency selector and the original source-race comparison; the registered source-race engine, signal parameters, evaluation times, legacy result, Oct10/11 freezes and Oct12–19 protocol remain unchanged.

Before execution, the worker creates a new output directory and registers the input five-file SHA256 set, full selected dependency inventory, wrapper/worker hashes, dependency-runner hash and timestamp. It materializes the exact frozen closure, verifies observation-code and input hashes in the subprocess, temporarily wraps the unchanged summary function, and retains baseline and candidate receipts independently. The original summary result is returned unchanged, including its known reason-count collision; corrected source/reason buckets remain in a separate receipt.

Only after both arm receipts reconcile and input hashes remain unchanged does it write the original comparison, separate receipts and a completion record binding output/row hashes to the registration. Existing output evidence is not overwritten. A failure leaves no valid completion. These outputs are explicitly development evidence; rank7 and formal OOS admission remain null. Receipt preservation alone does not establish real fills, queue priority, continuous cash coverage or independent profitability.

## Reproducible use

```bash
python3 research/polymarket-5m/run_source_race_reason_audit.py \
  --archive /path/to/strict_capture \
  --out-dir /path/to/new_research_output
```

The new worker and full outer execution environment must be bound before any later financial use. An old frozen result cannot be retroactively upgraded by the new receipt. No market-data replay was run with this entry point in this increment.

## Checks completed

- Seven new synthetic integration tests pass. They check unchanged two-arm comparison output against the pinned real summary functions, independent complete receipts, restoration after exceptions, missing/extra summary calls, count drift, code/input drift before replay, and non-overwrite behavior. They do not constitute whole-engine market-data parity or strategy backtests.
- The first test invocation unintentionally discovered five prior imported tests as well as the seven new ones; all12passed. Discovery was corrected, then all7newtests passed. New unique tests remain7; unique cases exercised this increment including prior tests are12.
- The original freeze SHA256 remains `29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63`. Dependency audit resolves18/18 with only the two already registered immutable substitutions, `eu_strict.py` and `poly_clob_rec.py`. The CLI help also runs successfully. This is dependency/engineering verification, not profitability.
- Worker SHA256: `caab0e86224341d3d09eb3e4c4d4370ca98a2858b2025c38baac898f8979147c`; test SHA256: `32c56bde99d018d94fa827f980fd2578bd4d30831bf1751319ec5ad89c8b2565`.
- Code commits: `fb7b588f4b2ae6cbdb590d9eec9ee0aad9549a11`, `fc3680dc4a0bcaab38070f9e548925b7bf38ffb1`.

## Native399393 transport increment

Part2 (artifact12509370125) and part4 (12508128268) were newly downloaded and verified at outer-ZIP, inner-ZIP and payload levels. Together with the previously verified part0, verified parts are now0/2/4 out of0–4. Part1 and part3 failed both their individual requests and final retries with MCP request timeouts: six large-artifact requests this increment, two succeeded/four timed out. This is a transient transfer problem, not proof of damaged data.

At2026-10-23T15:00:36UTC, authoritative Actions inventory still had only six native part/index artifacts. Record job120366669738 succeeded; analyze job120470340323 remained in Resolve, convert and test. No converted strict archive had been published. Source head remained4abdc4b7200fb9fabbc5dc573b55ff99e0c44c5e when checked; source progress/prd and package/PDF versions were unchanged.

The declared whole source is2,078,115,840bytes, SHA256 `02a4311302488c916729f708fc13cc1a82abdf8f8456f43134ccf62be9e598a2`; the whole source remains **unverified**. Interval, BTC coverage, official labels, paper ledgers, flow quality and financial PnL all remain unknown/null. Do not parse around missing parts or infer a complete day/week. Original native parts expire around2026-10-24T14:29UTC. Verified transport fragments remain local; they are not a persistent full-input mirror. Private URLs and payloads are excluded from this report and Git.

No new financial replay, candidate, actual fill, full recorded UTC day, rank7 row or formal OOS result was produced. Qualified independent mechanisms remain0/10. No order, production change, workflow dispatch or message was sent.
