# Replay input-quality audit (read-only)

This command reports **why a five-minute slot is not usable** before anyone
computes a strategy PnL. It does not change frozen strategies, source archives,
existing normalized tapes or the economic replay engine.

Run against an **already ingested and SHA-verified** replay cache:

```bash
cd research/polymarket-5m
python replay_data_quality.py \
  --cache /path/to/replay-cache/datasets/<dataset_sha256> \
  --required-features r_5s,r_15s \
  --max-quote-age-ms 250 \
  --out /path/to/new-quality-output
```

Output: `quality.json` with per-day reason counts, original input quality
issues, data identity and zero PnL; `slots.csv` with all **288 UTC slots per
calendar day** between the declared first/last observation.

The single `primary_blocker` field is a *priority-ordered diagnostic*, not a
claim that the other inputs are good. A slot may have several problems.
Categories distinguish:

- no declared recording span / only partial declared span
- missing market registration
- missing book events or no fresh Up/Down book
- no decision events or missing/noncausal required features
- missing official outcome
- input checks passed (NOT execution or feed continuity certified)

Important: this is an **existence-of-at-least-one-fresh-book-event** test,
not a proof that the exact strategy decision and its later matching time had a
fresh executable book. The execution engine retains its own per-order gates.
A native two-sided book can use its aggregate health and event receipt clock;
limited Up-only books require their own Up receipt/health and can never invent
a Down book. Source-event timestamps are never substituted for local receipts.
No feature, missing outcome, or recording gap is interpolated.

Keep AWS raw capture, S3 archived objects, derived GitHub lake partitions and
replay cache as **different inventories**. This tool only audits the last of
those. It cannot prove whether the AWS host was running, or recover an
unrecorded market. New external datasets must be separately imported and
verified before they can affect any score.

Economic benchmark remains USD 1,000 initial cash, target 100 shares per order,
partial FAK, and separate 500/600/1000 ms scenarios. This tool reports no
new backtests, rankings or profit. Use the normal replay runner for those.
