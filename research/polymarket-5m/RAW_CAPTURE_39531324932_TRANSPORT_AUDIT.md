# Raw capture 39531324932 transport audit

Audit time: 2026-10-21T06:25Z  
Scope: transport integrity only; no market-coverage, replay, PnL, or OOS admission.

## Published index

- Index artifact: `12324612994`
- Index artifact SHA256: `3a47639c48c1c454a0b156785c7c5b60892497bf1c7ec1b52cb83b34387e54c4`
- Declared source size: `1,665,781,760` bytes
- Declared source SHA256: `9f023368b227cb5d263cad1bdbd761c1e85509f24694908265e30ebbeb52f1a8`
- Publication status in index: `all_parts_uploaded`
- Admission in index: transport only, not market coverage or economic validation.

## Part verification

| Part | Artifact ID | Outer artifact digest | Declared/observed inner ZIP SHA256 | Declared payload SHA256 | Observed payload SHA256 | Result |
|---|---:|---|---|---|---|---|
| 00000 | 12324338710 | dd814bd7…3be9e0 | e86ef248…0ffe3a | c2f729e0…31ab89 | c2f729e0…31ab89 | pass |
| 00001 | 12324568114 | d4985148…0d820 | fc3c54bb…d3a8fd | c0bdefe1…75cb6 | c0bdefe1…75cb6 | pass |
| 00002 | 12324368716 | 2036237f…7db9b | 178c58ef…7210f | 3788dc5c…632c35 | 3788dc5c…632c35 | pass |
| 00003 | 12324523221 | 96cbf76f…761ff | 7b23e246…5e2c4 | e31cab0e…ef91c3 | 9e6de65d…99243 | **fail** |

The final inner ZIP has the exact byte count and ZIP SHA recorded by the publisher, but it has no end-of-central-directory record. Recovering its stored member from the local header yields a payload hash different from the published payload hash. Streaming all four observed payloads produces SHA256 `6f48d2efcb9fff68e57cdffbc1780ec1c6dc91886851f66f2635c4a7c84681d0`, not the declared source hash, and the TAR ends unexpectedly.

Therefore the public four-part handoff is not independently reconstructable as published. The in-run analysis may still use runner-local bytes, but that does not repair the external evidence package. Do not treat this capture as durable replay evidence until the final part is republished and the reconstructed source hash matches the index.

## Current execution state

- Recording job completed successfully and started successor run `39553701104`.
- Analyze job remains in progress; no strict BTC replay result has been admitted.
- Library persistence of the four part files failed at transfer, so Actions remains the only current byte source.
- No live orders, deployment, production changes, or frozen-rule changes.
- Seven-day BTC5m rank PnL remains null; qualified count remains 0/10.
