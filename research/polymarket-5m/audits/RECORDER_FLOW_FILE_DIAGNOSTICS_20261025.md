# Recorder flow file diagnostics

2026-10-25 UTC. Development engineering evidence; no new economic replay.

The existing recorder_futures_flow.audit_file catches standalone recorder_receipt failures, but tape.add_trade is outside its exception handler after standalone validation. Conflicting exact trade identities, connection-epoch changes and receive-clock regression can therefore terminate the file audit before a diagnostic result is returned. Two synthetic fixtures reproduce the old failure for conflicting IDs and epoch change. No frequency or occurrence in real inputs is inferred.

New recorder_flow_file_audit.py wraps the existing parser and RecorderFuturesFlowTape without modifying either engine or any frozen protocol. Every source gzip is hash-checked before parsing and requires explicit development scope. JSON/row-shape, standalone receipt and stateful tape errors are distinguished. The first rejection retains its line number, stage and raw-line hash. Subsequent standalone-parseable rows are counted as diagnostic only; tape.add_trade is never called again after the first rejection. Original gzip remains untouched. Counts of tape calls are explicitly not counts of unique trades.

Seven synthetic tests passed: conflicting trade ID, connection epoch switch, receive-clock regression, malformed JSON/non-object, invalid quantities with stable first failure, exact duplicate without false unique/coverage claims, and hash/development guards. Existing recorder Git blob e1c97eb5ff43126744dea50d66b8e8e07e8eca5c matches the research-head file used by the tests. Dependency SHA256 values are retained in the private checkpoint. This audit does not make a repaired/filtered tape, features, coverage certificates, signals, fills or PnL. Gzip/Unicode/I/O failures still raise and must not be treated as a clean audit. Input immutability during the read is an execution-environment precondition; this diagnostic command does not provide a sealed execution boundary.

From research/polymarket-5m in the registered code revision:

```bash
python3 recorder_flow_file_audit.py /absolute/eligible-development/venue.jsonl.gz --sha256 EXPECTED_SOURCE_SHA256 --capture-run-id EXACT_CAPTURE_RUN_ID --development --output /absolute/output/diagnostic.json
```

Use an already consumed development capture only. This new entry point was not run on any real or reserved capture this round. A no-detected-error result remains coverage-unverified. Real native coverage and existing target100shares/$1000 FAK execution must still be bound before the four registered USD-M variants can yield any financial result. No existing candidate rules, code freezes, results or selection history changed.

Upstream source advanced by one cumulative report-only commit to7bbea6c7aeac629be7a32b0bab522cb3410b3a1a. The nine changed forward report/ledger paths were recorded by identity only; financial patches were not exposed to model or used for tuning. No source code/progress/prd change in this delta. The raw401860 artifacts remain available and already queued for preservation before2026-10-26T14:46Z; no duplicate large download was done.

New real input audits0,financial replays0,candidate registrations0,full UTC days admitted0,Rank7 PnLnull,formal OOSnull,qualified independent mechanisms0/10. Missing results remain null. Seven consecutive complete UTC days/2016slots and all independent$1000,target100share scenarios are still required. ReservedOct24–Oct31market content and prior log-exposure provenance remain unchanged. Research only: no orders, production changes, AWS access, deployment, workflow dispatch or messages.
