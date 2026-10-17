# Raw capture quarantine audit

Run `python capture_quarantine_audit.py --root RESTORED_CHECKPOINT --out NEW_AUDIT.json`
after the existing `restore_published_archive.py` and `capture_checkpoint.py`
identity and member checks. Offline only; no network or credentials.

This audit checks every declared member's exact size/hash, exhausts gzip streams
to EOF/CRC, counts opaque JSONL records and inspects metadata-only stream fields.
It does not expose quote/decision values, construct features, train models,
calculate signed order flow, execute strategies or calculate profit.

Runtime duration, writer drain and heartbeat state do not establish continuous
per-token feed coverage. Opaque streams are not semantically JSON-validated.
The output therefore does not grant execution-input, full-day, seven-day or OOS
admission. A malformed/truncated stream or member mismatch aborts the audit.
Error stream summaries retain categories/counts, not messages or credentials.

Raw data inside an existing protected confirmation window must remain isolated
until its unchanged frozen protocol can be applied. A new collector identity or
new schema does not automatically inherit that protocol. No result is filled
with zero merely because data remain pending.

Tests: `python -m unittest discover -s . -p test_capture_quarantine_audit.py -v`
These are engineering tests, not financial backtests.
