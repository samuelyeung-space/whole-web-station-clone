"""Observe original trigger return branches, without changing the frozen rule.

The copied method calls a scalar-only observer at its original return sites.
No predicate is reimplemented and no future event or settlement label is read.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import inspect
import math
from pathlib import Path
import textwrap

EXPECTED_ENGINE_SHA256 = "1d15596f4b63b78b2e843b112040e4842b961a41e158efcdbd2ebea1d891f2c4"
RETURN_REASONS = ("nonpositive_price", "source_time_regressed", "exact_duplicate",
                  "outside_signal_window", "reference_unavailable", "sigma_or_move_gate",
                  "candidate")


def number(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class TriggerObserver:
    def __init__(self):
        self.pending = None
        self.sequence = 0
        self.joined = 0
        self.outcomes = Counter()
        self.clamped = Counter()

    def returned(self, frame, reason, result):
        if self.pending is not None:
            raise ValueError("unjoined trigger return")
        if reason == "reference_unavailable":
            reason = "reference_missing" if frame["reference_index"] < 0 else "reference_stale"
        if reason == "sigma_or_move_gate":
            reason = "sigma_not_finite" if not math.isfinite(frame["sigma"]) else "move_below_or_equal_threshold"
        source = frame["source"]
        raw_ms = float(frame["timestamp_ms"])
        effective_s = frame.get("timestamp_s")
        clamped = effective_s is not None and effective_s > raw_ms / 1000
        self.sequence += 1
        self.outcomes[(source, reason)] += 1
        self.clamped[source] += int(clamped)
        self.pending = {
            "schema": "h-trigger-return-v1", "kind": "trigger_gate",
            "audit_trigger_sequence": self.sequence, "source": source,
            "reason": reason, "raw_source_ms": number(raw_ms),
            "effective_trigger_ms": number(effective_s * 1000) if effective_s is not None else None,
            "source_time_clamped": clamped, "candidate_present": result is not None,
            "candidate_slot": int(result.slot) if result is not None else None,
            "candidate_source_ms": number(result.timestamp_s * 1000) if result is not None else None,
            "candidate_direction": int(result.direction) if result is not None else None,
            "sigma": number(frame.get("sigma")), "move": number(frame.get("move")),
            "minimum_z": number(frame.get("minimum_z")),
            "is_order_attempt": False, "pnl": None, "financial_result": None,
        }
        return result

    def join(self, upstream):
        """Join only after the current merged event; never use iterator lookahead."""
        if upstream["source"] is None:
            if self.pending is not None:
                raise ValueError("trigger return leaked into maintenance event")
            return None
        row = self.pending
        if row is None:
            raise ValueError("missing trigger return")
        for key in ("source", "candidate_present", "candidate_slot", "candidate_source_ms", "candidate_direction"):
            if row[key] != upstream[key]:
                raise ValueError(f"trigger/upstream identity mismatch: {key}")
        if row["raw_source_ms"] != upstream["event_source_ms"]:
            raise ValueError("trigger/upstream source timestamp mismatch")
        if (row["reason"] == "candidate") != row["candidate_present"]:
            raise ValueError("trigger result classification mismatch")
        row.update({k: upstream[k] for k in ("audit_event_sequence", "event_kind", "event_seq", "event_recv_ms")})
        row["upstream_reason"] = upstream["reason"]
        self.pending = None
        self.joined += 1
        return row

    def reconcile(self, source_counts):
        if self.pending is not None or self.joined != self.sequence:
            raise ValueError("incomplete trigger joins")
        counts = Counter()
        for (source, _), count in self.outcomes.items():
            counts[source] += count
        if dict(counts) != dict(source_counts):
            raise ValueError("trigger/source event count mismatch")
        return {"trigger_calls": self.sequence, "joined_source_events": self.joined,
                "outcomes": [{"source": s, "reason": r, "count": n}
                             for (s, r), n in sorted(self.outcomes.items())],
                "source_time_clamped": dict(self.clamped),
                "all_source_events_joined_exactly_once": True,
                "complete_trigger_return_branch_ledger": True,
                "pre_normalization_parser_rejections_covered": False,
                "clob_individual_update_rejections_covered": False,
                "financial_result": None}


def instrument(engine):
    if hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest() != EXPECTED_ENGINE_SHA256:
        raise ValueError("trigger engine identity drift")
    original = engine.TimestampedFirstTrigger
    tree = ast.parse(textwrap.dedent(inspect.getsource(original._candidate)))
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    returns.sort(key=lambda n: (n.lineno, n.col_offset))
    if len(returns) != len(RETURN_REASONS) or any(
            isinstance(n, ast.Name) and n.id == "_h_trigger_return" for n in ast.walk(tree)):
        raise ValueError("unexpected trigger return shape")
    for node, reason in zip(returns, RETURN_REASONS):
        node.value = ast.Call(func=ast.Name(id="_h_trigger_return", ctx=ast.Load()),
                              args=[ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[]),
                                    ast.Constant(reason), node.value or ast.Constant(None)], keywords=[])
    ast.fix_missing_locations(tree)
    observer = TriggerObserver()
    namespace = dict(original._candidate.__globals__)
    namespace["_h_trigger_return"] = observer.returned
    exec(compile(tree, "<hash-bound-trigger-observation>", "exec"), namespace)
    observed = type("ReturnObservedTrigger", (original,), {"_candidate": namespace["_candidate"]})
    return observed, observer
