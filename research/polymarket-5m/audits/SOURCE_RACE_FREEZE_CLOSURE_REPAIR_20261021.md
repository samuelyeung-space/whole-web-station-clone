# Source-race freeze closure repair — 2026-10-21

## Finding

The research branch contained the correct immutable dependency snapshots, but its
copy of `forward/source-race-freeze.json` was not the registered freeze. Two
dependency entries (`eu_strict.py` and `poly_clob_rec.py`) had been replaced and
the file fingerprint had changed from `29ad6623…` to `ddb147fa…`. Consequently,
the frozen-dependency adapter could not resolve the registered evaluator even
though the required historical bytes were present.

This was packaging drift in the research branch, not a strategy result. It does
not turn the previously failed source-race jobs into zero-PnL observations.

## Repair and verification

- Restored the byte-identical freeze and fingerprint from source commit
  `404d653491b321e2aa8376f6aba7085ce6c149a7`.
- Recomputed freeze SHA-256:
  `29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63`.
- Audited all 18 registered dependencies: 16 resolve from the current checkout;
  `eu_strict.py` and `poly_clob_rec.py` resolve from immutable snapshots; zero
  dependencies remain unresolved.
- Added an integration regression that checks the real source-race dependency
  closure, not only synthetic fixtures. Six unit tests pass.

The old strategy, cutoff, thresholds and frozen rules were not changed. No
financial replay was executed in this repair, so Rank7 PnL and formal OOS remain
`null`; qualified independent mechanisms remain 0/10.
