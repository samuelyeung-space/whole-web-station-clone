# Frozen MFA / MEM input restoration

This restores already-consumed development evidence. It does not fetch market data,
fit a model, evaluate signals, upgrade OOS status, or change a frozen strategy.

Required persistent inputs:

- Original source archive containing the six recorded enriched endpoint tapes.
- The existing minimal-feature result package containing its frozen score bank.
- `restoration/frozen_mfa_recipe.json` from the updated result package.
- For MEM, its existing `minimal_error_motion/feature_overlay.json`.
- Research code at the version recorded in the restoration evidence, with the
  original projection helper matching the recipe's source hash.

Use fresh output paths. The first step does not need an original MFA cache:

```bash
python restore_frozen_mfa.py --archive SOURCE_ARCHIVE.zip --package RESULTS.zip --recipe frozen_mfa_recipe.json --out restored_mfa
python minimal_error_delta.py restore --base restored_mfa/events.jsonl.gz --delta feature_overlay.json --out restored_mem.jsonl.gz
python replay_lab.py import --input restored_mem.jsonl.gz --input-type normalized --cache-root replay_cache
```

The base restorer verifies the archive, each source member, frozen score bank,
projection code, original enriched headers, per-source canonical event hashes,
source ordering, record counts, and target logical and compressed hashes. It
retains official outcomes, native books, constraints, timestamps and missingness.
Wrong versions fail instead of substituting another dataset or retraining.

The compressed-byte identity check also binds compressor behavior. A different
runtime that produces different gzip bytes is not silently accepted, even if a
logical comparison might later prove equivalence. Preserve the failure and use
an explicit engineering review, never alter an old confirmation protocol.

Source ordering comes from the original manifest, not alphabetic endpoint names;
equal-receipt ordering is preserved. Imported model code is used only for its
original execution-input projection; no estimator is fitted or loaded.

Restoration is an engineering action, not a new economic trial. Existing replay
results remain content-addressed and should not be rerun or counted again merely
because their input cache was restored. Use the replay-lab full-day coverage guard
for any request for seven complete days: restoration does not manufacture gaps.
