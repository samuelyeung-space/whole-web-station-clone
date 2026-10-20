# Low-disk opaque archive integrity check

2026-10-20. Research intake only; no signals, fitting, replay, orders or OOS.

`capture_archive_integrity.py` supplements the existing checkpoint restore and
directory quarantine checks when free disk cannot hold another full extracted
copy. It checks an explicit archive byte length and SHA256, regular/unique
safe allowlisted members, exact manifest inventory, each compressed member's
size/hash, gzip magic and full EOF/CRC, and final JSONL newline. It rechecks
the original archive hash after scanning. No raw member is extracted.

The existing producer's `capture_checkpoint.py` is copied unchanged into this
research branch solely to reuse its allowlist contract (original Git blob
`b5417fa0534c2abe0a9c2fbe92c83af1b9db9710`). It does not change a recorder,
signal or execution module and does not deploy a workflow.

Only root runtime/health objects and BTC5m registration slugs are interpreted.
Quote/decision and other gzip streams remain opaque byte scans; their JSON
semantics and prices are deliberately not parsed. A true empty gzip is valid
as bytes but certifies no market coverage; a zero-byte file with a `.gz` name
is refused. Registration count is not complete per-market coverage, official
outcome validation, live feed health, execution admission or financial evidence.

```sh
python capture_archive_integrity.py --archive CAPTURE.tar --sha256 EXPECTED_SHA256 --bytes EXPECTED_BYTES --out NEW_AUDIT.json
python -m unittest test_capture_archive_integrity -v
```

Fourteen synthetic fault tests cover bad whole/member hashes and sizes,
unmanifested/duplicate/nonregular/unsafe paths, missing gzip bytes, damaged
footer/CRC, missing newline, genuine empty streams and opaque non-JSON quote
bytes. They are engineering tests, not strategy trials or real backtests.

Storage remains separate: this module never uploads, deploys, obtains
credentials or retries a denied save route. `durable_saved=false` stays false
until an independently authorized persistence workflow actually succeeds.
No old freeze, input schema, strategy rule or execution assumption changes.
