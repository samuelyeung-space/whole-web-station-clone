# BTC 5m unified research replay

`replay_lab.py` is the offline entrypoint for **local recording → immutable data cache → strategy plugins → execution scenarios → full reports**. It reuses the existing receipt-clock execution economics in a separate audit fork. Original Oct10/11 frozen code and protocols are unchanged.

## One command

Run inside `research/polymarket-5m` on branch `codex/btc5m-mine500-20261010`:

```sh
python replay_lab.py pipeline --input recording.zip --cache-root cache --strategy 'mine500_catalog:*' --shares 5 --out runs
```

This accepts a downloaded GitHub recording ZIP (including nested `raw.tgz`), a TGZ, an extracted directory with `bundle-btc/strict`, or normalized `.jsonl[.gz]`. It never fetches credentials, accesses S3, downloads or places orders. The raw adapter verifies CLOB and receipt sequence, retains Gamma field availability, explicitly projects valid BTC spot data, preserves missing aggressor fields, and uses the existing adapter. Raw conversion can take minutes; it is done once per content/code identity. The fast strategy path reads the normalized cache.

Before publishing a raw conversion, and on every reuse, the adapter reads the full compressed stream through its trailer, checks every JSON record, the unique first dataset header, and exact event counts. A truncated stream is rejected even if its file hash matches a stored manifest. Failed conversion inputs and diagnostics remain available; corrupt cached bytes are never silently overwritten.

For multiple nonoverlapping recordings, list every local input after `--input`. Distinct overlapping observation spans are rejected rather than double counted. Byte-identical/logically identical normalized tapes are deduplicated. Corruption is rejected and never silently overwritten. An incompatible old recorder schema produces a retained `failed.json` with null PnL, not invented usable data.

## Prepare once, add strategies repeatedly

```sh
python replay_lab.py import --input day1.jsonl.gz day2.jsonl.gz --cache-root cache
# Copy cache_path from the command result:
python replay_lab.py inspect --cache cache/datasets/CONTENT_HASH
python replay_lab.py run --cache cache/datasets/CONTENT_HASH --strategy 'mine500_catalog:*' --shares 5,10,20 --stresses base,half_depth,plus_1cent --out runs
```

The original 500 plus supplementary 220 catalog entries are registered separately. Missing flow/fair/TWAP inputs remain blocked. Parameter variants are not independent mechanisms. The default total decision-to-match latencies are 500/600/1000ms, including venue wait: no extra 150ms is added. A projected input only admits its explicitly supported sampling latencies.

## Historical multi-day batch entrypoint

`historical_replay_batch.py` imports a list of recordings, merges admitted inputs
chronologically, and calls the same replay engine **once on the merged tape**.
Cash is continuous across recording fragments within that run. It does not add
the profit or independently reset cash balances of separate fragment runs.
The batch manifest retains every input, admission failure, source hash, coverage
gap and output run path. An input failure permits explicitly partial-input
development diagnostics; it does not silently certify a complete batch.

These commands run from `research/polymarket-5m`. Input paths below are local
downloaded recordings. The exclusive development boundary is part of the
request: the examples use recordings ending before `2026-10-10T00:00:00Z`.
Already-consumed historical data is allowed for development; these commands do
not inspect or consume a later frozen confirmation window.

For strict-schema recordings, omit historical relaxation flags:

```sh
python historical_replay_batch.py \
  --kind raw --input recordings/strict/*.zip \
  --batch-dir batches/strict_history \
  --development-end-before 2026-10-10T00:00:00Z \
  --strategy mine500_catalog:M500-439 \
  --strategy mine500_catalog:M500-669 \
  --strategy mine500_catalog:M500-677 \
  --mode isolated --shares 5 --cash 1000 \
  --latencies-ms 500,600,1000 --stresses base \
  --out runs/strict_history
```

`--kind normalized --input normalized/strict/*.jsonl.gz` reuses existing strict
normalized tapes instead of decoding the ZIPs again. Only tapes explicitly
marked `development` and ending before the supplied boundary are admitted.
Official unresolved labels stay missing; no Binance direction is substituted.
The date boundary is not a claim that every prior day was recorded.

### Old native Up-L1 recordings: explicit historical sensitivity

Old published recordings that contain native Up best-bid/ask observations use
`legacy_up_l1_adapter.py`. This reads native source/local receipt clocks, not
sampled depth snapshots, and does not manufacture a Down book from `1 - Up`.
The first stage for an individual legacy archive is:

```sh
python legacy_up_l1_adapter.py \
  --archive recordings/legacy/recording-36807987110.zip \
  --out normalized/legacy/recording-36807987110.jsonl.gz \
  --settlement-cutoff-ms 1792108800000
```

The example cutoff is `2026-10-16T00:00:00Z`. Use one predeclared cutoff later
than every admitted market end for the entire batch, not a separate cutoff for
each file. All final label/cash availability is withheld until this cutoff.
This is a conservative capital scenario, not observed redemption timing. The
adapter uses recorded Gamma outcome labels, while fee/tick/minimum-size values
from retrospective Gamma metadata are explicitly assumed valid during each
market because contemporaneous metadata availability was not recorded.
The adapter writes an immutable normalized tape plus a provenance manifest;
an existing output is not overwritten. Repeat for each selected archive.

Then run the complete selected legacy batch:

```sh
python historical_replay_batch.py \
  --kind normalized --input normalized/legacy/*.jsonl.gz \
  --batch-dir batches/legacy_history \
  --development-end-before 2026-10-10T00:00:00Z \
  --strategy mine500_catalog:M500-439 \
  --strategy mine500_catalog:M500-669 \
  --strategy mine500_catalog:M500-677 \
  --mode isolated --shares 5 --cash 1000 \
  --latencies-ms 500,600,1000 --stresses base \
  --side-specific-books --allow-assumed-constraints \
  --out runs/legacy_history
```

Both flags are required for this historical sensitivity lane:

| Option | Meaning |
|---|---|
| `--side-specific-books` | Evaluate only the actually recorded side. Up orders may use native Up L1; missing Down books produce retained failed/blocked attempts. |
| `--allow-assumed-constraints` | Run the explicitly declared historical constraint upper-bound mode. Supplied tick/minimum-size values still apply; missing constraint values remain unvalidated and are not invented. Invalid supplied values remain rejected. |

These flags do not turn a legacy tape into strict execution evidence. Source
sequence/connection continuity, contemporaneous parameter clocks, actual order
rejections and private fills remain unobserved. The inherited assumptions and
limitations remain in `audit.json` and the readable report. Keep strict and
legacy runs separate so their coverage and economic assumptions remain visible.
If a market lacks a tick or minimum-size value as well as its provenance, this
mode does not prove that the simulated order was legal at the venue. Check the
retained constraint audit before interpreting any apparent profit.

For the same batch's **shared $1000** run, repeat the preceding legacy batch
command with `--mode shared`, `--batch-dir batches/legacy_shared`, and
`--out runs/legacy_shared`. Its component priority is frozen by the listed
strategy order; it submits at most one first order per market. It is a new
joint account simulation, not the sum of the three isolated candidate PnLs.

To evaluate the entire already-registered grid on an admitted merged cache,
replace the three `--strategy` arguments by `--strategy 'mine500_catalog:*'`.
That evaluates all 720 registered candidate rows, including explicitly blocked
rows for missing features. It does not claim 720 independent mechanisms or
720 executable strategies. Changing strategy rules or feature definitions
requires a new run identity and cannot inherit the Oct10/11 freezes.

### Consolidated multi-day diagnostics

Use actual completed content-addressed `run_path` values returned above:

```sh
python historical_batch_summary.py \
  runs/strict_history/STRICT_RUN_HASH \
  runs/legacy_history/LEGACY_RUN_HASH \
  runs/legacy_shared/SHARED_RUN_HASH \
  --out diagnostics/historical_batch
```

This reads reports without rerunning any strategy. It validates completion and
input hashes and reconciles attempt/settlement/cash records. It writes
`summary.json`, `summary.csv`, `daily.csv`, `cash_daily.csv` and `report.md`.
Every run, scenario, candidate and latency retains its own row. Overlapping
markets across runs are disclosed; returns and balances are never added.

- Every UTC date has **288** theoretical five-minute slots; seven consecutive
  dates have **2016**. Registered markets, recorded span, data-present slots,
  attempts, fills and failures are distinct counts.
- `net_pnl_usd` in the consolidated report requires full recording coverage of
  all represented calendar dates and complete execution. Missing dates cannot
  create a false consecutive-day pass.
- `recorded_scope_net_pnl_usd` retains the engine's economic result for the
  observed recording scope; `settled_subset_pnl_usd` separately reports known
  settlements while unresolved positions remain incomplete.
- Per-settled-share returns use only settled shares in the denominator. Missing
  dates and feature-blocked candidates are not inserted as zero PnL samples.
- Cash-event daily drawdown uses settlement event dates and carried realized
  equity. Strategy daily PnL uses market-start dates. Neither is fabricated
  marked-to-market equity.
- When at least two observed dates exist, seeded date-cluster bootstrap ranges
  are descriptive development diagnostics only. They do not correct historical
  strategy selection, prove independence, or qualify an OOS edge.

Run `python -m unittest test_historical_batch_summary -v` for the focused
incomplete-data, calendar-gap, nonadditivity, hash and cash-account checks.

## Strategy plugin interface

A plugin is an importable Python module with `catalog()` (also `candidates()` or `make_candidates()`) and `signal(candidate, features)`. Candidate IDs and required features must be explicit. A signal returns `Up`, `Down`, or `None`. Only required values with locally available timestamps are passed in. New module directories use `--plugin-dir`.

```python
def catalog():
    return [{"id": "EXAMPLE-1", "required_features": ["r_5s"]}]

def signal(candidate, features):
    # Illustration only; this duplicates an existing momentum family.
    if 60 <= features["tau_s"] <= 120 and features["r_5s"] >= 1:
        return "Up"
    return None
```

Use `--strategy my_strategy:EXAMPLE-1` or `my_strategy:*`. Each run writes candidate rules, plugin/source and engine hashes, parameters and input identity **before** executing. Plugin source bytes are freshly compiled; old imported functions or `.pyc` cannot silently masquerade as a new version. Repeating identical inputs/rules/configuration verifies and reuses the completed result; changing them creates a new registered run.

JPR/MAR modules use the same interface. Their additional fields must already exist in the tape. For the previously frozen feature/model workflow, use `cached_strategy_replay.py prepare --endpoint ... --prices ... --scores ... --cache feature-cache`, then pass its `events.jsonl.gz` to `replay_lab.py import`. This does not fit a new model. A new feature definition requires a new causal preprocessing artifact; missing columns are never synthesized by this runner.

## Shared $1000, fixed 100-share order requests

```sh
python replay_lab.py run --cache cache/datasets/CONTENT_HASH \
  --strategy mine500_catalog:M500-439 --strategy mine500_catalog:M500-669 --strategy mine500_catalog:M500-677 \
  --mode shared --shares 100 --cash 1000 --latencies-ms 500,600,1000 \
  --stresses base,half_depth,plus_1cent --release-delays-ms 0,60000,300000 --out runs
```

The component list above is a **previously selected development diagnostic**, not an independently validated recommendation. Shared mode chooses the earliest eligible component signal, breaking same-decision ties by listed priority. It sends one order per market, never retries a failed first order and never spends the same depth twice for competing rules. Each latency is a separate counterfactual portfolio. `--priority` can explicitly freeze all selected candidate IDs in order.

Default execution is partial FAK at the decision-time fixed limit. `--full-or-zero` is a separate full-depth sensitivity, not a claim that live FAK guarantees full fills. Fees, tick/min-size constraints, stale/unhealthy/missing books, market close, incomplete match horizon and cash reservations are checked. Size 100 is the order request; a partial fill may be below 100. Cash release delays are explicit assumptions after local knowledge of official outcome, not observed redemption times.

## Reports and coverage

Each content-addressed run includes `registration.json`, `completion.json`, `coverage.json`, aggregate `metrics.json`, and per-scenario:

| File | Meaning |
|---|---|
| `metrics.csv` | Every candidate/latency, signals, submissions, full/partial/failed orders, fees, share PnL, ending cash and realized drawdown |
| `daily.csv` | UTC market-start date, theoretical 288 slots, recorded span, complete span, available market data, signals and PnL |
| `markets.csv` | Every registered market, including no-signal and blocked cases |
| `attempts.jsonl.gz` | Every first attempt including limit, both clocks, rejection reason, partial execution and settlement |
| `cash_ledger.csv` | Actual engine event sequence: reservation, release, debit, settlement and payout availability |
| `audit.json` | Field validity, overlap/suppression, component attribution, constraints, hashes, report reconciliation |
| `report.md` | Readable coverage and result summary |

UTC dates containing data are not automatically whole days. Seven days have **2016** five-minute slots. `--require-full-days 7` refuses the request unless seven consecutive dates have full recorded spans and all 288 distinct slots contain registered market/book/decision/official-outcome records. This presence gate still does not prove continuous feed health. No gaps are filled with OHLC or assumed quotes. Blocked/unresolved PnL remains null.

## Execution scope and verification

Supported: long Up/Down taker entries, held to official settlement; isolated or one-order-per-market shared portfolios. The reusable interface rejects maker, short, dynamic hedge and scale-in contracts; existing specialized queue/inventory research is not silently promoted into validated fills. These require their matching event inputs and dedicated execution backend.

All fills remain displayed-depth upper-bound simulations. Realized drawdown is provided; marked-to-market drawdown is not fabricated. Candidate, size, delay and stress alternatives are correlated and must not be added. This development runner cannot inherit an old OOS freeze or certify profitability.

```sh
python -m unittest discover -p 'test_replay_*.py' -v
```

Acceptance results, measured durations, real-data reconciliation and exact code identity are recorded separately in `REPLAY_ACCEPTANCE_20261016.md` / `replay_acceptance_20261016.json`. No raw/private event data is committed to Git.
