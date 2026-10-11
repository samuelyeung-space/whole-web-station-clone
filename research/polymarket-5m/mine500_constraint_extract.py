"""Extract receipt-causal order limits without editing the frozen adapter.

Gamma orderMinSize is known only at _recorded_at_ms. Tick size comes from the
direct token's CLOB snapshot or explicit tick_size_change, never a market-wide
Gamma tick or the final Gamma snapshot. Disconnects invalidate observed ticks.

Legacy strict recorders omitted tick_size_change. Snapshot-only output must not
claim complete dynamic-tick coverage. Optional raw CLOB streams recover explicit
changes, but this module cannot prove their recording was complete.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import heapq
import itertools
import json
import math
from pathlib import Path

VERSION = "mine500-constraint-extract-v1"
FIELDS = ("min_order_size", "up_tick_size", "down_tick_size")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value, *, tick=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 and (not tick or value < 1) else None


def read_jsonl(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for index, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"non-object row {index}")
            yield row


def market_maps(normalized):
    """Copy only identity/receipt fields; settlement outcomes are not inspected."""
    result = {}
    for event in normalized:
        if event.get("type") != "market":
            continue
        selected = {name:event[name] for name in (
            "market_id", "recv_ms", "start_ms", "end_ms", "up_token_id", "down_token_id")}
        prior = result.get(selected["market_id"])
        if prior and any(prior[name] != selected[name] for name in selected if name != "recv_ms"):
            raise ValueError("market/token identity changes")
        if prior is None or selected["recv_ms"] < prior["recv_ms"]:
            result[selected["market_id"]] = selected
    return result


def _ordered(rows, *, origin):
    previous = -math.inf
    for index, row in enumerate(rows):
        now = row.get("recv_ms")
        if not _number(now) or now < previous:
            raise ValueError(f"nonmonotone or missing {origin} receipt clock")
        previous = now
        yield dict(row, _constraint_origin=origin, _constraint_index=index)


def _raw_tick_rows(rows):
    """Only explicit changes; raw snapshot duplicates are deliberately excluded."""
    for index, outer in enumerate(rows):
        messages = outer.get("msg", outer)
        if not isinstance(messages, list):
            messages = [messages]
        for message_index, msg in enumerate(messages):
            if not isinstance(msg, dict) or msg.get("event_type") != "tick_size_change":
                continue
            now = outer.get("recv_ms")
            try:
                source = float(msg["timestamp"])
            except (TypeError, ValueError, KeyError):
                source = None
            yield {"kind":"clob_tick_size_change", "recv_ms":now,
                   "source_ts_ms":source, "market_id":msg.get("market"),
                   "asset_id":str(msg.get("asset_id", "")),
                   "new_tick_size":msg.get("new_tick_size"),
                   "old_tick_size":msg.get("old_tick_size"),
                   "seq":index*100000+message_index}


def _field_event(mid, now, name, value, source, origin, sequence=None, reason=None):
    event = {"type":"constraint", "market_id":mid, "recv_ms":now,
             name:value, name+"_available_ms":now, name+"_effective_ms":now,
             name+"_source":source, "constraints_source":source,
             "constraint_origin":origin, "constraint_seq":sequence}
    if reason:
        event["constraint_reason"] = reason
    return event


def _coalesce(events, audit):
    """Collapse exact-time updates; unknown cross-stream order fails closed."""
    events.sort(key=lambda event:(event["recv_ms"], event["market_id"]))
    result = []
    for (now, mid), group in itertools.groupby(events, key=lambda e:(e["recv_ms"],e["market_id"])):
        fields = defaultdict(list)
        for event in group:
            for name in FIELDS:
                if name in event:
                    fields[name].append(event)
        out = {"type":"constraint", "market_id":mid, "recv_ms":now}
        provenance = {}
        ambiguous = []
        for name, observations in fields.items():
            values = {observation[name] for observation in observations}
            origins = {observation["constraint_origin"] for observation in observations}
            sequences = [observation["constraint_seq"] for observation in observations]
            sequence_proven = (len(origins) == 1 and all(type(s) is int for s in sequences)
                               and len(set(sequences)) == len(sequences))
            chosen = (max(observations, key=lambda observation:observation["constraint_seq"])
                      if sequence_proven else observations[-1])
            value = chosen[name]
            if len(values) > 1 and not sequence_proven:
                value = None
                ambiguous.append(name)
                audit["ambiguous_exact_time_fields"] += 1
            for suffix in ("_available_ms", "_effective_ms", "_source"):
                out[name+suffix] = chosen[name+suffix]
            out[name] = value
            provenance[name] = [{k:event.get(k) for k in (
                "constraint_origin", "constraint_seq", "constraint_reason")}
                for event in observations]
        out["constraint_provenance"] = provenance
        if ambiguous:
            out["constraint_ambiguous_fields"] = ambiguous
        result.append(out)
    return result


def extract_constraints(markets, gamma_rows, clob_rows, raw_clob_rows=None):
    """Return (sparse constraint events, audit) without any forward fill."""
    tokens = {}
    for mid, market in markets.items():
        for side in ("up", "down"):
            token = str(market[side+"_token_id"])
            if token in tokens:
                raise ValueError("token maps to multiple markets or sides")
            tokens[token] = (mid, side)
    audit = Counter()
    events = []
    for row in gamma_rows:
        audit["gamma_rows"] += 1
        mid = row.get("conditionId")
        if mid not in markets:
            continue
        now = row.get("_recorded_at_ms")
        if not _number(now):
            audit["gamma_missing_local_receipt"] += 1
            continue
        if now < markets[mid]["recv_ms"]:
            audit["gamma_before_known_market"] += 1
            continue
        if "orderMinSize" not in row:
            audit["gamma_missing_minimum"] += 1
            continue
        events.append(_field_event(mid,now,"min_order_size",_positive(row["orderMinSize"]),
                                   "gamma","raw_gamma"))
        audit["gamma_minimum_observations"] += 1
    streams = [_ordered(clob_rows, origin="strict_clob")]
    if raw_clob_rows is not None:
        streams.append(_ordered(_raw_tick_rows(raw_clob_rows), origin="raw_clob_ticks"))
    source_clock = {}
    observed = set()
    merged = heapq.merge(*streams, key=lambda row:row["recv_ms"])
    for row in merged:
        audit["clob_rows"] += 1
        kind, now = row.get("kind"), row["recv_ms"]
        if kind in ("clob_connection", "clob_error", "clob_disconnect"):
            for token in sorted(observed):
                mid, side = tokens[token]
                events.append(_field_event(mid, now, side+"_tick_size", None, "clob",
                                           row["_constraint_origin"],row.get("seq"),
                                           "connection_boundary_tick_unknown"))
            source_clock.clear()
            observed.clear()
            continue
        token = str(row.get("asset_id", ""))
        if token not in tokens:
            continue
        mid, side = tokens[token]
        if row.get("market_id") not in (None, "", mid):
            raise ValueError("CLOB token/market identity mismatch")
        if now < markets[mid]["recv_ms"]:
            audit["clob_before_known_market"] += 1
            continue
        source = row.get("source_ts_ms")
        is_tick = kind in ("clob_tick_size_change", "tick_size_change")
        has_tick = is_tick or (kind == "clob_snapshot" and "tick_size" in row)
        if not _number(source):
            if has_tick:
                events.append(_field_event(mid,now,side+"_tick_size",None,"clob",
                    row["_constraint_origin"],row.get("seq"),"missing_source_clock"))
                observed.add(token)
                audit["tick_missing_source_clock"] += 1
            continue
        if source < source_clock.get(token, -math.inf):
            audit["regressed_source_rows_ignored"] += 1
            continue
        source_clock[token] = source
        if not has_tick:
            continue
        raw_value = row.get("new_tick_size") if is_tick else row["tick_size"]
        value = _positive(raw_value, tick=True)
        events.append(_field_event(mid,now,side+"_tick_size",value,"clob",
                      row["_constraint_origin"],row.get("seq"),
                      "explicit_tick_change" if is_tick else "snapshot_tick"))
        observed.add(token)
        audit["explicit_tick_changes" if is_tick else "snapshot_tick_observations"] += 1
    result = _coalesce(events, audit)
    audit["constraint_events"] = len(result)
    return result, {"version":VERSION, "counts":dict(audit),
        "tick_update_stream_complete":False,
        "tick_coverage_limitation":"Observed snapshot/change values only; recording completeness is not established. Legacy strict converters discard tick_size_change.",
        "same_time_merge_assumption":"Metadata/constraints precede book/decision at identical local recv_ms; cross-stream same-field conflicts become null when their sequence is not proven.",
        "gamma_tick_used":False, "missing_values_policy":"explicit invalidation or absent; never infer"}


def merge_constraints(normalized, constraints):
    """Same receipt: dataset/market, constraints, then original book/decision order."""
    def grouped(rows, origin):
        rows = _ordered(rows, origin=origin)
        for now, group in itertools.groupby(rows, key=lambda r:r["recv_ms"]):
            for row in sorted(group, key=lambda r:0 if r.get("type") == "dataset" else
                              1 if r.get("type") == "market" else
                              2 if r.get("type") == "constraint" else 3):
                row.pop("_constraint_origin", None)
                row.pop("_constraint_index", None)
                yield row
    def key(row):
        return (row["recv_ms"], 0 if row.get("type") == "dataset" else
                1 if row.get("type") == "market" else
                2 if row.get("type") == "constraint" else 3)
    yield from heapq.merge(grouped(normalized,"normalized"), grouped(constraints,"constraints"), key=key)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--gamma-markets", required=True, type=Path)
    parser.add_argument("--strict-clob", required=True, type=Path)
    parser.add_argument("--raw-clob", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--write-merged", action="store_true")
    args = parser.parse_args(argv)
    if args.out.exists():
        raise ValueError("refuse to replace an existing extraction")
    hashes = {}
    for name in ("normalized", "gamma_markets", "strict_clob", "raw_clob"):
        path = getattr(args,name)
        if path:
            with path.open("rb") as handle:
                hashes[name] = hashlib.file_digest(handle,"sha256").hexdigest()
    markets = market_maps(read_jsonl(args.normalized))
    constraints, audit = extract_constraints(markets,read_jsonl(args.gamma_markets),
        read_jsonl(args.strict_clob),read_jsonl(args.raw_clob) if args.raw_clob else None)
    args.out.mkdir(parents=True)
    with gzip.open(args.out/"constraints.jsonl.gz","wt") as handle:
        for row in constraints:
            handle.write(json.dumps(row,allow_nan=False)+"\n")
    if args.write_merged:
        with gzip.open(args.out/"normalized_with_constraints.jsonl.gz","wt") as handle:
            for row in merge_constraints(read_jsonl(args.normalized), constraints):
                handle.write(json.dumps(row,allow_nan=False)+"\n")
    audit.update(input_hashes=hashes, market_count=len(markets),
                 code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (args.out/"audit.json").write_text(json.dumps(audit,indent=2)+"\n")
    print(json.dumps(audit))


if __name__ == "__main__":
    main()
