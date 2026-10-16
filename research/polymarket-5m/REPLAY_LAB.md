# BTC 5m unified research replay

`replay_lab.py` is the offline entrypoint for **local recording → immutable data cache → strategy plugins → execution scenarios → full reports**. It reuses the existing receipt-clock execution economics in a separate audit fork. Original Oct10/11 frozen code and protocols are unchanged.

## One command

Run inside `research/polymarket-5m` on branch `codex/btc5m-mine500-20261010`:

```sh
python replay_lab.py pipeline --input recording.zip --cache-root cache --strategy 'mine500_catalog:*' --shares 5 --out runs
```

This accepts a downloaded GitHub recording ZIP (including nested `raw.tgz`), a TGZ, an extracted directory with `bundle-btc/strict`, or normalized `.jsonl[.gz]`. It never fetches credentials, accesses S3, downloads or places orders. The raw adapter verifies CLOB and receipt sequence, retains Gamma field availability, explicitly projects valid BTC spot data, preserves missing aggressor fields, and uses the existing adapter. Raw conversion can take minutes; it is done once per content/code identity. The fast strategy path reads the normalized cache.

For multiple nonoverlapping recordings, list every local input after `--input`. Distinct overlapping observation spans are rejected rather than double counted. Byte-identical/logically identical normalized tapes are deduplicated. Corruption is rejected and never silently overwritten. An incompatible old recorder schema produces a retained `failed.json` with null PnL, not invented usable data.

## Prepare once, add strategies repeatedly

```sh
python replay_lab.py import --input day1.jsonl.gz day2.jsonl.gz --cache-root cache
# Copy cache_path from the command result:
python replay_lab.py inspect --cache cache/datasets/CONTENT_HASH
python replay_lab.py run --cache cache/datasets/CONTENT_HASH --strategy 'mine500_catalog:*' --shares 5,10,20 --stresses base,half_depth,plus_1cent --out runs
```

The original 500 plus supplementary 220 catalog entries are registered separately. Missing flow/fair/TWAP inputs remain blocked. Parameter variants are not independent mechanisms. The default total decision-to-match latencies are 500/600/1000ms, including venue wait: no extra 150ms is added. A projected input only admits its explicitly supported sampling latencies.

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
