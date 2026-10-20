# Prepared artifact byte ranges

Research-only transfer helper for a file URL already returned by the authorized
GitHub connector. It neither discovers nor obtains credentials and does not
change recorder/workflow/strategy/production configuration.

A normal GET can return HTTP206 with only the first16MiB even though the
artifact is419,430,662bytes. EOF alone is not proof of a complete download.
The helper requests explicit8MiB ranges with at most4 workers, checks exact206
Content-Range/total/length and body boundaries, then verifies the independently
supplied full artifact SHA256 before atomically naming the completed file.
Artifacts are bounded to512MiB; use the original publisher's index/parts for
larger archives. Existing files are never overwritten. Failures remain pending.

Read the prepared URL, expected exact size and SHA256 from JSON on stdin.
Do not place signed URLs in source control, command arguments, receipts or
research reports. The CLI prints sanitized error type/code only. Hashes and
byte ranges are recorded without the URL.

```sh
python -m unittest test_artifact_range_download -v
python artifact_range_download.py --out NEW_PART.zip --receipt NEW_RECEIPT.json < PRIVATE_TRANSFER.json
```

Six offline adversarial tests cover ignored/incorrect ranges, total/length
mismatches, short/excess bodies, wrong assembled SHA, configuration bounds and
preservation of existing complete/partial files. Real transfer receipt must
verify the artifact digest; engineering tests are not market evidence.

Transport verification remains separate from publisher-inner ZIP/payload SHA,
checkpoint member hashes, complete-archive restoration, gzip CRC, market
coverage and causal execution input admission. Verifying one of five parts
must never be reported as validating the full archive or a complete UTCday.
