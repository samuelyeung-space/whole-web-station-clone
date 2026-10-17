# Read-only replay preflight

`replay_input_preflight.py` checks a pinned `dependencies_sha256` manifest and
reports all missing/drifting files rather than only the first mismatch. The
expected manifest digest must be retained independently. It never updates the
freeze, restores production files or grants OOS eligibility.

Optional raw or strict JSONL inputs are scanned to EOF/gzip CRC. The numeric
reported-BBO guard uses the existing Family34 inequalities: any supplied side
must be finite and strictly inside `(0,1)`; supplied bid must be below ask.
Conversion, boundary and locked/crossed failures are reported separately.
No values are clipped, filled, removed or made into synthetic liquidity.

Only counts, reason labels, file hashes and bounded frame-hash samples are
emitted. No quote values, signals, outcome labels, features, trades or PnL are
emitted. Raw diagnostics do not apply registration, token, connection, epoch
or strategy eligibility filters and therefore do not identify the precise
first exception of a complete strategy replay. They are not opportunity or
execution counts. Passing this diagnostic does not certify feed coverage or
seven-day/ranking/OOS admission.

Example:

```
python replay_input_preflight.py --root PINNED_SOURCE \
  --freeze PINNED_SOURCE/forward/source-race-freeze.json \
  --expected-freeze-sha256 TRUSTED_SHA \
  --input BTC_RAW.jsonl.gz --mode raw --out NEW_REPORT.json
```

`--mode strict` supports logical price-change rows and physical change batches.
Exit2 means a dependency mismatch or input-contract issue was found. Output is
exclusive: prior reports are not overwritten. Gzip corruption, malformed rows
and changed input bytes keep the scan incomplete.

When reproducing a historical freeze, use a separate read-only source snapshot
whose **entire** dependency map matches. Never roll back a shared collector or
garbage-collection lane: other frozen families can require the newer version.
A matching source snapshot still needs the original data, region, runtime and
full-day/statistical gates. New semantics require a new later untouched window.

Tests are engineering counterexamples, not financial backtests:

```
python -m unittest discover -s . -p test_replay_input_preflight.py -v
```
