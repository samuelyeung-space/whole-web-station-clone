# Oct3 legacy depth admission audit — 2026-10-16

Four newly ingested archives; 5,746,296 envelope rows; 224 retrospectively registered markets. New-batch book-slot coverage is 220/288 on Oct3 UTC, not 220 complete markets. This excludes the prior batch tail.

| Matching offset from whole-second decision | Diagnostic queries | Both states available, unambiguous | Best-price mismatch | L1 price or size mismatch |
|---|---:|---:|---:|---:|
| 500 ms | 64792 | 45109 | 9441 (20.93%) | 42899 (95.10%) |
| 600 ms | 64792 | 45131 | 10897 (24.15%) | 43121 (95.55%) |
| 1000 ms | 64575 | 45457 | 0 (0.00%) | 0 (0.00%) |

## Interpretation

These are paired historical book queries, NOT signals, sends, fills, or independent observations. No PnL was calculated. Comparisons use latest receipt strictly before query time, and reject conflicting equal-receipt states. Invalid/empty/crossed books and ambiguous timestamps remain in diagnostic denominators; the complete counts are in the JSON.

The full-depth bundle retains the last update per token per second. It is sparse at 500/600 ms. The event-level observation stream retains Up top-of-book price and first-level size whenever those values change, not Down or all depth. The zero mismatch at the next whole second is consistent with the export construction; it is NOT proof of true fills or superior 1000 ms execution.

No mirror fabrication was established: the pinned parser updates each asset independently. Do not synthesize Down depth from Up or infer aggressor direction from price.

Missing mapping receipt and settlement/cash-availability clocks still bar unchanged strict replay. Retrospective labels/metadata may be retained for a separately registered diagnostic experiment, but cannot be backdated into decision information. This audit does not claim all old data are unusable, nor invalidate unchanged later raw-event tapes.

## Reproduce

Download the four pinned artifact IDs in `legacy_oct3_depth_evidence_20261016.json`, verify SHA256, run `legacy_recording_cache.py`, then `legacy_depth_admission.py --cache <archive-cache> --out <audit.json>`. Run `python3 -m unittest test_legacy_depth_admission -v` (7 synthetic tests passed). Code commit: `be3097365eec54552b2ae238b4b860ec56a21aa0`.

Source semantics pinned to `eeb25299c9a856f15f42e15f763f5ffb4882310d`, `research/polymarket-5m/recording.py` (`book_events`, `LatencyFiles`) and `paper_trader.py` (`apply_clob_message`). Upstream strategy branch remains `7aa7767d1484dce44bb9d250e8c835702074ca8f`; runs 38838476036 and 38847381329 returned no downloadable artifacts at this check.

## Resume

Do not rerun this intake as new evidence. Next: remaining inventory dates, especially raw event-level archives; causal legacy Up-L1 diagnostic adapter with explicit unknowns; recover contemporaneous metadata and dual-token execution evidence where actually recorded. Preserve Oct10/11 freezes. No seven-day strategy result yet; qualified mechanisms remain 0/10.

The original package and PDF were not replaced in this run. This Git audit and the state checkpoint are the incremental deliverables; no raw data are committed.
