"""Hash-verified, bounded-memory historical MIX capacity audit; never trades or fits.

Examples::
  python historical100_runner.py --segments C --workdir /tmp/h100 --out /tmp/result
  python historical100_runner.py --segments B C --workdir /tmp/h100 --out /tmp/result

The second command uses one continuous account per rule/latency across B and C.
Both were previously examined: this is historical capacity revalidation, not a
new untouched OOS experiment. Raw archives are deleted after their one-day pass.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tarfile
import time
import traceback

HERE = Path(__file__).resolve().parent
SEGMENTS = {"B": ("2026-07-16", "2026-08-16"), "C": ("2026-08-16", "2026-08-30")}
VERSION = "historical100-verified-archive-runner-v1"


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def write_gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(clean(row), separators=(",", ":"), allow_nan=False) + "\n")


def timestamp_ms(value):
    """Accept Unix seconds or milliseconds only; reject unrecognised epochs."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if 1_000_000_000 <= x < 10_000_000_000:
        return x * 1000
    if 1_000_000_000_000 <= x < 10_000_000_000_000:
        return x
    return None


def book_path(root, market):
    return Path(root) / (hashlib.sha256(str(market).encode()).hexdigest() + ".jsonl.gz")


class DiskBooks(Mapping):
    """No in-memory multi-month tape; the execution engine supplies its own LRU."""
    def __init__(self, root, markets):
        self.root, self.markets = Path(root), set(map(str, markets))

    def __getitem__(self, key):
        if str(key) not in self.markets:
            raise KeyError(key)
        with gzip.open(book_path(self.root, key), "rt") as f:
            return [json.loads(line) for line in f]

    def __iter__(self):
        return iter(self.markets)

    def __len__(self):
        return len(self.markets)


def levels(prices, sizes):
    if prices is None or sizes is None:
        return []
    # Keep malformed values for the execution engine to reject, not silently fix.
    if len(prices) != len(sizes):
        return [[None, None]]
    return [[clean(p), clean(q)] for p, q in zip(prices, sizes)]


def append_books(root, rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row["market_id"]), []).append(row)
    for mid, records in grouped.items():
        with gzip.open(book_path(root, mid), "at", encoding="utf-8") as f:
            for row in records:
                snap = {"recv_ms": row.get("timestamp_ms"),
                        "active": row.get("lifecycle_state") == "active",
                        "halted": row.get("observed_halt_flag"),
                        "clock_semantics": "archive_100ms_snapshot_grid_not_wire_receipt"}
                for side in ("up", "down"):
                    snap[side] = {"bids": levels(row.get(side + "_side_bids"), row.get(side + "_bid_sizes")),
                                  "asks": levels(row.get(side + "_side_asks"), row.get(side + "_ask_sizes"))}
                f.write(json.dumps(clean(snap), separators=(",", ":"), allow_nan=False) + "\n")


def finalise_book(path):
    """Causal top-state last-change clock from ALL observations before compression."""
    with gzip.open(path, "rt") as f:
        rows = [json.loads(line) for line in f]
    # Archive append order resolves repeated grid timestamps, as original MIX.
    unique = {row["recv_ms"]: row for row in rows}
    rows = [unique[t] for t in sorted(unique)]
    last, changed = None, None
    for row in rows:
        state = []
        for side in ("up", "down"):
            token = row[side]
            for name in ("bids", "asks"):
                lv = token.get(name, [])
                valid = [x for x in lv if len(x) == 2 and isinstance(x[0], (int, float))]
                top = (max if name == "bids" else min)(valid, key=lambda x: x[0]) if valid else None
                state.append(top)
        if state != last:
            changed, last = row["recv_ms"], state
        row["changed_ms"] = changed
    write_gz(path, rows)
    return len(rows)


def read_archive(path, workdir, books_dir):
    """Read selected columns in 32K Arrow batches; never inflate a daily market table."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq
    import cross
    import jump2s_hf as hf

    features, markets, resolutions, trades = [], [], [], []
    counts, mids = Counter(), set()
    market_cols = list(dict.fromkeys(cross.MKT_COLS + ["timestamp_ms", "fee_rate", "min_order_size"]))
    feature_cols = list(dict.fromkeys(cross.FEAT_COLS + ["slug"] +
                                    [s + "_side_" + k for s in ("up", "down") for k in ("asks", "bids")]))
    columns = {"polymarket_features_100ms": feature_cols,
               "polymarket_market_100ms": market_cols,
               "trades": ["trade_ts_ms", "recv_ts_ms", "exchange", "price"], "resolution": None}
    tmp = Path(workdir) / "one_member.parquet"
    with tarfile.open(path, "r|gz") as tar:
        for member in tar:
            match = re.search(r"dataset=([^/]+)", member.name)
            table = match.group(1) if match else ""
            if not member.isfile() or not member.name.endswith(".parquet") or table not in columns:
                continue
            with tar.extractfile(member) as source, tmp.open("wb") as dest:
                shutil.copyfileobj(source, dest, length=1 << 20)
            pf = pq.ParquetFile(tmp)
            if table == "polymarket_features_100ms":
                required = {"timestamp_ms", "market_id", "slug", "lifecycle_state", "observed_halt_flag"}
                required.update(s + "_" + suffix for s in ("up", "down")
                                for suffix in ("side_bids", "bid_sizes", "side_asks", "ask_sizes"))
                absent = required - set(pf.schema_arrow.names)
                if absent:
                    raise ValueError("incomplete 100-share depth/health schema in " + member.name + ": " + str(sorted(absent)))
            want = columns[table]
            have = None if want is None else [x for x in want if x in pf.schema_arrow.names]
            for batch in pf.iter_batches(batch_size=32768, columns=have):
                counts[table + "_raw_rows"] += batch.num_rows
                df = batch.to_pandas()
                if table in ("polymarket_features_100ms", "polymarket_market_100ms"):
                    if "slug" not in df:
                        raise ValueError("cannot establish BTC5m coverage: missing slug")
                    df = df[df["slug"].astype(str).str.match(r"^btc-updown-5m-\d+$")].copy()
                    if df.empty:
                        continue
                    df["market_id"] = df["market_id"].astype(str)
                if table == "polymarket_features_100ms":
                    rows = df.to_dict("records")
                    append_books(books_dir, rows)
                    mids.update(df["market_id"])
                    slim = {c: df[c] for c in cross.FEAT_COLS if c in df and not c.endswith("_sizes")}
                    for side in ("up", "down"):
                        for kind in ("ask", "bid"):
                            col = side + "_" + kind + "_sizes"
                            slim[side + "_" + kind + "_size"] = df[col].map(
                                lambda v: float(v[0]) if v is not None and len(v) else np.nan) if col in df else np.nan
                    features.append(pd.DataFrame(slim))
                    counts["btc5m_feature_rows"] += len(df)
                elif table == "polymarket_market_100ms":
                    markets.append(hf.shrink_markets(df))
                    counts["btc5m_market_rows"] += len(df)
                elif table == "resolution":
                    resolutions.append(df)
                else:
                    if "exchange" not in df:
                        raise ValueError("trade source missing")
                    df = df[df.exchange.astype(str).str.lower().eq("binance")]
                    trades.append(df.drop(columns="exchange"))
                    counts["binance_trade_rows"] += len(df)
            del pf
            tmp.unlink()
            hf._release()
    def cat(parts):
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    feat, mk, rs, bn = cat(features), cat(markets), cat(resolutions), cat(trades)
    mk = hf.shrink_markets(mk)
    if not bn.empty:
        bn = bn.drop_duplicates().sort_values("recv_ts_ms", kind="stable").reset_index(drop=True)
    return feat, mk, rs, bn, dict(counts), mids


def official_markets(mk, raw_rs):
    import numpy as np
    import cross
    import jump2s_hf as hf
    rs, provisional = hf.final_resolution(raw_rs)
    if mk.empty:
        return mk, {}, {"provisional_excluded": provisional}
    # Periodic market fields cannot override the accepted official resolution.
    mk = mk.copy()
    mk["up_won"] = np.nan
    mt = cross.market_table(mk, rs)
    finals = {}
    if not rs.empty and "market_id" in rs:
        for row in rs.to_dict("records"):
            finals[str(row["market_id"])] = row
    out, clock_counts = {}, Counter()
    for row in mt.to_dict("records"):
        if row.get("horizon") != 5:
            continue
        mid, end = str(row["market_id"]), float(row["end"])
        final = finals.get(mid, {})
        emitted = timestamp_ms(final.get("emitted_at_ts"))
        if emitted is not None and emitted < end:
            emitted = None
        up = row.get("up_won")
        outcome = "up" if up == 1 else "down" if up == 0 else None
        clock_counts["known_emitted_clock" if emitted is not None else "missing_emitted_clock"] += 1
        out[mid] = {"end_ms": end, "start_ms": float(row["start"]), "outcome": outcome,
                    "outcome_known_ms": emitted, "fee_rate": .07,
                    "fee_source": "unverified_historical_assumption_0.07_p_1minus_p",
                    "min_order_size": 5., "min_order_size_source": "historical_study_assumption",
                    "outcome_source": "archive_accepted_final_resolution" if outcome else None,
                    "outcome_clock_source": "producer_emitted_at_proxy_NOT_local_receipt" if emitted is not None else None,
                    "outcome_clock_raw": clean(final.get("emitted_at_ts")),
                    "actual_redemption_clock_known": False}
    return mt, out, {"provisional_excluded": int(provisional), **clock_counts}


def merge_markets(existing, incoming):
    """A later file missing its resolution must not erase a recorded final label."""
    for mid, new in incoming.items():
        old = existing.get(mid)
        if old is not None and old.get("outcome") is not None:
            if new.get("outcome") is not None and old["outcome"] != new["outcome"]:
                raise ValueError("conflicting accepted final outcomes across archives: " + mid)
            if new.get("outcome") is None:
                continue
            known = [x for x in (old.get("outcome_known_ms"), new.get("outcome_known_ms")) if x is not None]
            if known:
                # Latest accepted record avoids claiming an earlier availability
                # than the final record actually chosen from either archive.
                new = dict(new, outcome_known_ms=max(known))
        existing[mid] = new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", nargs="+", choices=("B", "C"), default=["C"])
    parser.add_argument("--latencies", nargs="+", type=int, default=[500, 600, 1000])
    parser.add_argument("--shares", type=float, default=100.)
    parser.add_argument("--initial-cash", type=float, default=1000.)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget-s", type=int, default=18000)
    parser.add_argument("--max-days", type=int)
    args = parser.parse_args(argv)
    if args.shares != 100 or args.initial_cash != 1000 or args.latencies != [500, 600, 1000]:
        raise ValueError("this registered experiment fixes 100 shares, $1000 and 500/600/1000ms")
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("output must be empty to prevent replacing evidence")
    args.workdir.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    books_dir = args.workdir / "books"
    books_dir.mkdir(exist_ok=True)
    if any(books_dir.iterdir()):
        raise ValueError("use a new empty workdir to avoid mixing raw inputs")
    import cross
    import jump2s as j2
    import jump2s_hf as hf
    import mix_hf as mix
    from historical100_execution import replay
    from historical100_signals import day_signals, load_frozen_scorer, model_identity

    started = time.time()
    registration = {"version": VERSION, "registered_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "historical_capacity_revalidation_NOT_new_untouched_OOS",
        "segments": sorted(set(args.segments)), "shares": args.shares, "initial_cash": args.initial_cash,
        "latencies_ms": args.latencies, "rules": ["R1", "R2", "R3"],
        "cash_accounts": "one continuous account for ALL selected dates per rule/latency; alternatives not additive",
        "training": "none; previously frozen A-trained canonical MIX model only",
        "selection": "all frozen threshold-selected unique decision rows, no future-fill or outcome eligibility filter",
        "price_limit": "decision ask fixed; FAK partial/failure; full request cash reserve; no borrowing/resizing/retry",
        "dataset": cross.DS, "download_integrity": "every archive exact MANIFEST byte size and SHA256 mandatory",
        "fee_assumption": "0.07 * filled_shares * p * (1-p); historical per-market fee unverified",
        "cash_release_assumption": "recorded final emitted_at_ts when available, else market end + 300s; not actual redeem",
        "snapshot_clock": "100ms recorder grid, not exact wire receive or matching-engine events",
        "capacity": "displayed depth upper bound with no claim of actual fills or validated price impact",
        "microstructure_rule_changes": "date-varying venue waiting not separately added to TOTAL 500/600/1000ms",
        "unresolved": "preserved in exposure; no fabricated zero PnL or immediate cash payout",
        "max_days": args.max_days, "new_qualified_strategies": 0, "formal_new_oos_result": None,
        "code_sha256": {p.name: sha256(p) for p in sorted(HERE.glob("historical100_*.py"))}}
    write_json(args.out / "registration.json", registration)
    scorer = load_frozen_scorer()
    registration["frozen_model_identity"] = model_identity()
    registration["dependencies_sha256"] = {name: sha256(HERE / name) for name in
        ("cross.py", "mix_hf.py", "mix_forward.py", "jump2s.py", "jump2s_hf.py", "binary.py", "pm_outcomes.py")}
    manifest_raw = cross.fetch("MANIFEST.txt")
    (args.out / "source_MANIFEST.txt").write_bytes(manifest_raw)
    manifest = manifest_raw.decode()
    digests = mix.manifest_sha(manifest)
    arcs = [(n, s) for n, s in cross.archives(manifest)
            if any(SEGMENTS[x][0] <= n[15:25] < SEGMENTS[x][1] for x in args.segments)]
    arcs.sort()
    if args.max_days is not None:
        arcs = arcs[:args.max_days]
    if not arcs:
        raise ValueError("selected manifest contains no archives")
    for name, size in arcs:
        if not size or not re.fullmatch("[0-9a-f]{64}", digests.get(name, "")):
            raise ValueError("missing exact archive size/hash: " + name)
    registration["manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
    registration["archives"] = [{"name": n, "bytes": s, "sha256": digests[n]} for n, s in arcs]
    registration["auxiliary_inputs"] = {p.name: sha256(p) for p in sorted(mix.INPUTS.glob("*.csv.gz"))}
    write_json(args.out / "registration.json", registration)
    factor, dvol = mix.load_inputs()
    if factor.empty or dvol.empty:
        raise ValueError("frozen-model auxiliary inputs absent")
    klines, missing = hf.klines(args.workdir)
    if missing:
        raise ValueError("complete original Binance trend kline inputs required, missing " + str(missing))
    write_json(args.out / "kline_provenance.json", {
        "files": [{"name": p.name, "sha256": sha256(p)} for p in sorted(args.workdir.glob("BTCUSDT-1m-*.zip"))],
        "usage": "trend features guarded by original scorer; never settlement labels"})
    all_signals, all_markets, days, failures, carry = [], {}, [], [], None
    retained_books = set()
    for name, size in arcs:
        if time.time() - started > args.budget_s:
            failures.append({"archive": name, "reason": "time_budget_unprocessed"})
            continue
        local = args.workdir / name
        try:
            print("DOWNLOAD " + name, flush=True)
            mix._fetch(cross, name, local, size=size, sha256=digests[name])
            feat, mk, rs, bn, counts, day_mids = read_archive(local, args.workdir, books_dir)
            local.unlink()
            mt, markets, label_audit = official_markets(mk, rs)
            day = name[15:25]
            sig, signal_audit = day_signals(feat, mt, bn, carry=carry, factor=factor,
                                          dvol=dvol, closes=klines, day=day, scorer=scorer)
            records = sig.to_dict("records") if hasattr(sig, "to_dict") else sig
            adapted = []
            for i, r in enumerate(records):
                row = dict(r)
                row.update({"decision_ms": float(r.get("t_ms", r.get("decision_ms"))),
                            "rule_id": r.get("rule", r.get("rule_id")), "market_id": str(r["market_id"])})
                row["signal_id"] = f'{row["rule_id"]}:{row["market_id"]}:{row["decision_ms"]:.6f}:{row["side"]}'
                adapted.append(clean(row))
            keep = {r["market_id"] for r in adapted}
            for mid in day_mids:
                path = book_path(books_dir, mid)
                if mid in keep or mid in retained_books:
                    finalise_book(path)
                    retained_books.add(mid)
                else:
                    path.unlink(missing_ok=True)
            all_signals.extend(adapted)
            merge_markets(all_markets, markets)
            if not bn.empty:
                latest = bn["recv_ts_ms"].max()
                carry = bn[bn.recv_ts_ms >= latest - mix.CARRY_S * 1000].copy()
            else:
                carry = None
            info = {"archive": name, "day": day, "sha256": digests[name], "bytes": size,
                    "status": "verified_parsed_scored", "selected_rows": len(adapted),
                    "market_count": len(markets), "label_audit": label_audit,
                    "signals": signal_audit, "counts": counts}
            days.append(clean(info))
            write_json(args.out / "coverage.json", {"days": days, "failures": failures,
                "requested_archives": len(arcs), "complete": len(days) == len(arcs)})
            print(f'DAY {day} markets={len(markets)} signals={len(adapted)} elapsed={time.time()-started:.1f}s', flush=True)
            del feat, mk, rs, bn, sig, mt
            gc.collect()
            hf._release()
        except Exception as exc:
            failures.append({"archive": name, "reason": repr(exc), "traceback": traceback.format_exc()})
            local.unlink(missing_ok=True)
            carry = None
            print(traceback.format_exc(), flush=True)
            # A failed day may have partly appended book rows. Abort execution;
            # keep coverage rather than trade across a silent recorder gap.
            break
    unique = {}
    for row in all_signals:
        key = row["signal_id"]
        if key in unique and unique[key] != row:
            raise ValueError("non-identical duplicate frozen signal across archives: " + key)
        unique[key] = row
    all_signals = sorted(unique.values(), key=lambda r: (r["decision_ms"], r["market_id"], r["signal_id"]))
    write_gz(args.out / "signals.jsonl.gz", all_signals)
    write_json(args.out / "markets.json", all_markets)
    coverage = {"requested_archives": len(arcs), "processed_archives": len(days), "days": days,
                "failures": failures, "complete": len(days) == len(arcs), "unique_signals": len(all_signals),
                "historical_results_previously_seen": True, "formal_new_oos_result": None}
    write_json(args.out / "coverage.json", coverage)
    if failures:
        write_json(args.out / "run_status.json", {"status": "input_incomplete_no_replay", "result": None})
        return 2
    books = DiskBooks(books_dir, retained_books)
    metrics = []
    for rule in ("R1", "R2", "R3"):
        for latency in args.latencies:
            result = replay(all_markets, books, all_signals, rule_id=rule, latency_ms=latency,
                            initial_cash=args.initial_cash, shares=args.shares, outcome_delay_ms=300000.)
            stem = f'{rule}_{latency}ms'
            for key in ("attempts", "exits", "cash_events"):
                write_gz(args.out / (stem + "_" + key + ".jsonl.gz"), result[key])
            metrics.append({"rule": rule, "latency_ms": latency, **result["summary"]})
            write_json(args.out / (stem + "_summary.json"), {k: result[k] for k in ("summary", "config")})
            print("SCENARIO " + stem + " " + json.dumps(clean(result["summary"])), flush=True)
    write_json(args.out / "metrics.json", metrics)
    write_json(args.out / "run_status.json", {"status": "historical_capacity_replay_complete",
        "alternatives": len(metrics), "new_qualified_strategies": 0, "formal_new_oos_result": None,
        "elapsed_s": time.time() - started, "cash_accounts_not_additive": True})
    write_json(args.out / "MANIFEST.json", {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
               for p in sorted(args.out.iterdir()) if p.is_file() and p.name != "MANIFEST.json"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
