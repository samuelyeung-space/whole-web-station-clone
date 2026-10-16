"""Read-only, receipt-causal replay data quality audit.

The declared observation span is NOT proof of continuous recording. This tool
never downloads, fills gaps, synthesizes Down quotes, or calculates PnL.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path

DAY = 86_400_000
SLOT = 300_000

def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def merged_intervals(intervals):
    result = []
    for a, b in sorted(intervals):
        if not valid_number(a) or not valid_number(b) or a >= b:
            raise ValueError("invalid observation interval")
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result

def span_status(intervals, start, end):
    # "full" is declared recorder span only, not feed/message completeness.
    overlaps = [[max(start, a), min(end, b)] for a, b in intervals
                if a < end and b > start]
    if not overlaps:
        return "absent"
    return "full_span" if len(overlaps) == 1 and overlaps[0] == [start, end] else "partial_span"

def summarize(events, intervals, required_features=(), max_quote_age_ms=250):
    """Stream normalized events into one fixed-size summary per UTC market slot."""
    if max_quote_age_ms < 0:
        raise ValueError("negative quote age")
    intervals = merged_intervals(intervals)
    if not intervals:
        raise ValueError("observation intervals required")
    if len(set(required_features)) != len(required_features):
        raise ValueError("duplicate required features")
    markets = {}
    slots = defaultdict(lambda: {"market_ids": set(), "book_events": 0,
        "up_fresh": False, "down_fresh": False, "decision_events": 0,
        "valid_feature_decisions": 0, "outcome_ids": set(),
        "missing_feature_counts": Counter()})
    counts = Counter()
    for event in events:
        kind = event.get("type")
        counts[kind] += 1
        if kind == "dataset":
            continue
        mid = event.get("market_id")
        if kind == "market":
            start = event.get("start_ms")
            if not valid_number(start) or int(start) != start or int(start) % SLOT:
                raise ValueError("market start must align with five-minute UTC grid")
            if mid in markets and markets[mid] != int(start):
                raise ValueError("conflicting market slot")
            markets[mid] = int(start)
        if mid not in markets:
            # A validated tape registers markets before their events.
            raise ValueError("event before market registration: " + str(mid))
        start = markets[mid]
        state = slots[start]
        state["market_ids"].add(mid)
        if kind == "book":
            state["book_events"] += 1
            now = event.get("recv_ms")
            for side in ("up", "down"):
                receipt = event.get(side + "_recv_ms")
                fresh = (valid_number(now) and valid_number(receipt)
                         and 0 <= now - receipt <= max_quote_age_ms
                         and event.get(side + "_healthy") is True)
                # Some native tapes have a single aggregate health flag; never
                # infer side-specific health from it or from a missing clock.
                if fresh and isinstance(event.get(side + "_asks"), list) and event[side + "_asks"]:
                    state[side + "_fresh"] = True
        elif kind == "decision":
            state["decision_events"] += 1
            now = event.get("recv_ms")
            features = event.get("features")
            available = event.get("feature_available_ms")
            features = features if isinstance(features, dict) else {}
            available = available if isinstance(available, dict) else {}
            missing = [name for name in required_features if not (
                valid_number(features.get(name)) and
                valid_number(available.get(name)) and
                valid_number(now) and available[name] <= now)]
            if not missing:
                state["valid_feature_decisions"] += 1
            for name in missing:
                state["missing_feature_counts"][name] += 1
        elif kind == "outcome":
            if event.get("winner") in ("Up", "Down"):
                state["outcome_ids"].add(mid)
    first = int(intervals[0][0] // DAY) * DAY
    last = int(math.ceil(intervals[-1][1] / DAY)) * DAY
    rows, daily = [], defaultdict(Counter)
    for start in range(first, last, SLOT):
        end = start + SLOT
        state = slots.get(start)
        coverage = span_status(intervals, start, end)
        if coverage == "absent":
            reason = "no_declared_recording_span"
        elif coverage == "partial_span":
            reason = "partial_declared_recording_span"
        elif not state:
            reason = "no_market_registration"
        elif not state["book_events"]:
            reason = "no_book_events"
        elif not state["up_fresh"]:
            reason = "no_fresh_up_book"
        elif not state["down_fresh"]:
            reason = "no_fresh_down_book"
        elif not state["decision_events"]:
            reason = "no_decision_events"
        elif not state["valid_feature_decisions"]:
            reason = "required_features_unavailable"
        elif not state["outcome_ids"]:
            reason = "no_official_outcome"
        else:
            reason = "input_checks_passed_not_execution_verified"
        day = datetime.fromtimestamp(start / 1000, timezone.utc).date().isoformat()
        row = {"date_utc": day, "slot_start_ms": start, "declared_span": coverage,
               "market_count": len(state["market_ids"]) if state else 0,
               "book_events": state["book_events"] if state else 0,
               "up_fresh_seen": bool(state and state["up_fresh"]),
               "down_fresh_seen": bool(state and state["down_fresh"]),
               "decision_events": state["decision_events"] if state else 0,
               "valid_feature_decisions": state["valid_feature_decisions"] if state else 0,
               "outcome_count": len(state["outcome_ids"]) if state else 0,
               "primary_blocker": reason}
        rows.append(row)
        daily[day][reason] += 1
        daily[day]["expected_slots"] += 1
        if coverage == "full_span":
            daily[day]["declared_full_span_slots"] += 1
        if reason == "input_checks_passed_not_execution_verified":
            daily[day]["input_ready_slots"] += 1
    return {"schema": "replay-data-quality-v1",
            "semantics": "Input audit only; full span is not feed health, and fresh quote is not a proven fill",
            "required_features": list(required_features),
            "max_quote_age_ms": max_quote_age_ms,
            "event_counts": dict(counts),
            "days": [{"date_utc": day, **dict(counts)} for day, counts in sorted(daily.items())],
            "slots": rows,
            "pnl": None, "formal_oos_result": None}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, help="Existing immutable replay cache directory")
    parser.add_argument("--out", required=True, help="New output directory; never overwrite")
    parser.add_argument("--required-features", default="", help="Comma-separated strategy feature names")
    parser.add_argument("--max-quote-age-ms", type=int, default=250)
    args = parser.parse_args(argv)
    import replay_dataset
    cache = Path(args.cache).resolve()
    manifest = replay_dataset.verify_cache(cache)
    features = tuple(x.strip() for x in args.required_features.split(",") if x.strip())
    result = summarize(replay_dataset.iter_events(cache),
                       manifest["coverage"]["observed_intervals_ms"],
                       features, args.max_quote_age_ms)
    result["dataset_key"] = manifest["dataset_key"]
    result["tape_sha256"] = manifest["tape_sha256"]
    result["source_quality_issues"] = manifest.get("quality_issues", {})
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    slots = result.pop("slots")
    (out / "quality.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    with (out / "slots.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(slots[0]) if slots else [])
        writer.writeheader()
        writer.writerows(slots)
    print(json.dumps({"quality": str(out / "quality.json"),
                      "slots": str(out / "slots.csv"), "days": len(result["days"]),
                      "new_backtests": 0, "pnl": None}))

if __name__ == "__main__":
    main()
