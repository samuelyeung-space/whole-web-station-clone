"""Audit complete GitHub CSV patches without replaying or silently rewriting history.

Only added/removed rows occur in the input patch; this cannot attest to the
integrity of unchanged file regions or independently verify settlement labels.
All economic numbers below describe published paper rows, never real fills.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime
import hashlib
import io
import json
import math
import re
from pathlib import Path

SCHEMAS = {
    "momentum5s.csv": (
        "market_id,start_ts,t,day,side,z5,lag,won,price,fee,size,filled,pnl,reason,coin,run",
        ("market_id", "lag")),
    "theta30.csv": (
        "market_id,day,decision_ms,sent,filled,reason,side_up,decision_ask,decision_mid_up,fixed_limit,fill_price,fill_fee,all_in_cost,won,pnl_per_share,pnl",
        ("market_id",)),
    "trades.csv": ("id,slug,start,side,price,fee,won,pnl,t,kind", ("id", "slug")),
    "paper_ledger.csv": (
        "slug,end,tau,side,ask,shares,fee,cost,won,pnl,mid_up,decided_ms",
        ("slug", "tau")),
}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
TOLERANCE = 1e-12

def changed_lines(patch):
    """Require every declared hunk to be complete before returning any rows."""
    added, removed = [], []
    need_old = need_new = old = new = None
    def finish():
        if need_old is not None and (old != need_old or new != need_new):
            raise ValueError("incomplete or malformed patch hunk")
    for line in patch.splitlines():
        if line.startswith("@@"):
            finish()
            match = HUNK.match(line)
            if not match:
                raise ValueError("invalid patch header")
            need_old, need_new = int(match[2] or 1), int(match[4] or 1)
            old = new = 0
        elif line == "\\ No newline at end of file":
            continue
        elif need_old is None:
            if line.startswith(("diff ", "index ", "--- ", "+++ ")):
                continue
            raise ValueError("unexpected patch preamble")
        elif line.startswith("+"):
            added.append(line[1:]); new += 1
        elif line.startswith("-"):
            removed.append(line[1:]); old += 1
        elif line.startswith(" "):
            old += 1; new += 1
        else:
            raise ValueError("unrecognized patch line")
    finish()
    if need_old is None:
        raise ValueError("no complete hunks")
    return added, removed

def same_value(a, b):
    if a == b:
        return True
    try:
        x, y = float(a), float(b)
        return math.isfinite(x) and math.isfinite(y) and math.isclose(
            x, y, abs_tol=TOLERANCE, rel_tol=0)
    except ValueError:
        return False

def rows_by_key(lines, header, keys):
    columns = header.split(",")
    output = {}
    for line in lines:
        fields = next(csv.reader([line]))
        if len(fields) != len(columns):
            raise ValueError("row/schema length mismatch")
        if fields == columns:
            raise ValueError("schema change requires a new audited adapter")
        row = dict(zip(columns, fields))
        key = tuple(row[k] for k in keys)
        if key in output:
            raise ValueError("duplicate identity in patch")
        output[key] = row
    return output

def compare_patch(patch, name):
    header, keys = SCHEMAS[name]
    add, remove = changed_lines(patch)
    new, old = rows_by_key(add, header, keys), rows_by_key(remove, header, keys)
    material = []
    numeric_only = 0
    for key in new.keys() & old.keys():
        fields = [k for k in new[key] if not same_value(new[key][k], old[key][k])]
        if fields:
            material.append({"key": list(key), "fields": fields,
                             "before": old[key], "after": new[key]})
        else:
            numeric_only += 1
    added = [new[k] for k in sorted(new.keys() - old.keys())]
    removed = [old[k] for k in sorted(old.keys() - new.keys())]
    return {
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "complete_hunks": True, "added_patch_lines": len(add),
        "removed_patch_lines": len(remove),
        "new_identities": len(added), "removed_identities": len(removed),
        "numeric_only_rewrites": numeric_only,
        "material_rewrites": material, "new_rows": added, "removed_rows": removed,
    }

def metrics(rows, name):
    for row in rows:
        if row.get('filled', 'True') != 'True':
            continue
        if name == 'momentum5s.csv':
            expected = float(row['won']) - float(row['price']) - float(row['fee'])
            actual = float(row['pnl'])
        elif name == 'theta30.csv':
            expected = (1 if row['won'] == 'True' else 0) - float(row['fill_price']) - float(row['fill_fee'])
            actual = float(row['pnl_per_share'])
        elif name == 'trades.csv':
            expected = (1 if row['won'] == 'True' else 0) - float(row['price']) - float(row['fee'])
            actual = float(row['pnl'])
        else:
            expected = (float(row['shares']) if row['won'] == 'True' else 0) - float(row['cost'])
            actual = float(row['pnl'])
        if not math.isfinite(actual) or not math.isclose(expected, actual, rel_tol=0, abs_tol=TOLERANCE):
            raise ValueError('published fill arithmetic does not reconcile')
    group_keys = {"momentum5s.csv": ("coin", "lag"),
                  "theta30.csv": (), "trades.csv": ("id",),
                  "paper_ledger.csv": ("tau",)}[name]
    groups = collections.defaultdict(list)
    for row in rows:
        slug = row.get("market_id", row.get("slug", ""))
        if name == "trades.csv" and not slug.startswith("btc-updown-5m-"):
            continue
        groups["/".join(row[k] for k in group_keys) or "theta30"].append(row)
    out = {}
    for key, rr in sorted(groups.items()):
        filled = [r for r in rr if r.get("filled", "True") == "True"]
        unit = "pnl_per_share" if name == "theta30.csv" else "pnl"
        pp = [float(r[unit]) for r in filled]
        days = {r.get('day') or datetime.datetime.fromtimestamp(
            float(r.get('market_id', r.get('slug')).rsplit('-', 1)[-1]),
            datetime.timezone.utc).date().isoformat() for r in rr}
        out[key] = {
            "rows": len(rr),
            "markets": len({r.get("market_id", r.get("slug")) for r in rr}),
            "recorded_fills": len(filled),
            "recorded_sends": sum(r['sent'] == 'True' for r in rr) if name == 'theta30.csv' else None,
            "observed_market_utc_days": sorted(days),
            "reasons": dict(collections.Counter(r.get("reason", "selected_row_only") for r in rr)),
            "recorded_pnl_column_sum": math.fsum(pp),
            "recorded_pnl_unit": ("USD" if name == "paper_ledger.csv" else "USD_per_one_share"),
            "recorded_five_share_pnl_usd": math.fsum(pp)*5 if name == "momentum5s.csv" else None,
            "mean_recorded_net_cents_per_share": math.fsum(pp)/len(pp)*100 if pp and name != 'paper_ledger.csv' else None,
            "day_cluster_uncertainty": None,
            "rank7_pnl_usd": None, "formal_oos_pnl_usd": None,
        }
    return out

def audit(payload):
    output = {"schema": "published-forward-patch-audit-v1",
              "source_head": payload["source_head"], "parent": payload["parent"],
              "tolerance": TOLERANCE, "files": {},
              "scope": "complete changed hunks only; no new financial replay",
              "official_labels_independently_verified": False,
              "new_row_pnl_arithmetic_checked": True,
              "full_recorded_utc_days_verified": 0, "new_qualified_mechanisms": 0}
    for item in payload["files"]:
        name = item["filename"].rsplit("/", 1)[-1]
        if name not in SCHEMAS:
            continue
        if not item.get("patch"):
            raise ValueError("required CSV patch absent")
        result = compare_patch(item["patch"], name)
        result["new_row_metrics"] = metrics(result["new_rows"], name)
        output["files"][name] = result
    if set(output["files"]) != set(SCHEMAS):
        raise ValueError("required patch file absent")
    return output

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("patch_json")
    args = ap.parse_args()
    print(json.dumps(audit(json.loads(Path(args.patch_json).read_text())),
                     indent=2, ensure_ascii=False, allow_nan=False))
