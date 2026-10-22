"""Hash-bound observation of gates before HReplay, without changing the engine.

One row describes one yielded merged event (CLOB batches stay batches).
`no_candidate` is deliberately opaque: this does not explain trigger internals.
The compiled research copy adds a finally callback around the original loop;
the source file, dependencies, execution rules and frozen protocols are intact.
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
SOURCES = {"spot_trade": "spot_trade", "futures_bbo": "futures_book_ticker",
           "futures_trade": "futures_trade", "deribit_quote": "deribit_quote"}
LIFECYCLES = {"spot_connection", "spot_disconnect", "futures_connection",
              "futures_disconnect", "futures_trade_connection",
              "futures_trade_disconnect", "deribit_connection", "deribit_disconnect"}
FAIR_REASONS = {"no_poly_history", "book_crossed_or_empty", "book_stale",
                "no_anchor", "no_fair", "trigger"}


def finite(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class UpstreamObserver:
    def __init__(self, sink):
        self.sink = sink
        self.sequence = 0
        self.outcomes = Counter()
        self.expected_counters = Counter()
        self.source_events = Counter()
        self.errors = 0

    def exit_event(self, frame, failed):
        """Called after this event, before the merged iterator advances again."""
        event = frame["event"]
        kind = event["kind"]
        self.sequence += 1
        source = SOURCES.get(kind)
        # A CLOB continue precedes the engine's candidate reset; never reuse it.
        candidate = frame.get("candidate") if source else None
        stage, reason = "rejected", None
        if failed:
            stage, reason = "error", "engine_exception"
            self.errors += 1
        elif kind == "clob_batch":
            stage, reason = "maintenance", "clob_batch"
            self.expected_counters["clob_batches"] += 1
        elif kind in LIFECYCLES:
            stage, reason = "maintenance", "source_lifecycle"
            self.expected_counters[kind] += 1
        elif source is None:
            stage, reason = "maintenance", "unhandled_kind"
            self.expected_counters[kind] += 1
        else:
            self.source_events[source] += 1
            counter = {"spot_trade": "spot_trades", "futures_bbo": "futures_bbo",
                       "futures_trade": "futures_trades", "deribit_quote": "deribit_quotes"}[kind]
            self.expected_counters[counter] += 1
            if source not in frame["enabled_sources"]:
                reason = "source_disabled"
                self.expected_counters[f"candidate_source_disabled_{source}"] += int(candidate is not None)
            elif candidate is None:
                reason = "no_candidate"
            elif not frame["connected"]:
                reason = "clob_disconnected"
            elif frame["market"] is None:
                reason = "no_market"
                self.expected_counters["candidate_no_market"] += 1
            else:
                reason = frame["reason"]
                if reason not in FAIR_REASONS:
                    raise ValueError(f"unregistered fair gate: {reason}")
                self.expected_counters[f"candidate_{reason}"] += 1
                self.expected_counters[f"candidate_source_{source}"] += 1
                if reason == "trigger":
                    stage = "eligible"
        self.outcomes[(stage, reason, source)] += 1
        reached_fair = source is not None and not failed and reason in FAIR_REASONS
        market = frame.get("market") if reached_fair else None
        row = {"schema": "h-upstream-event-v1", "kind": "upstream_gate",
               "audit_event_sequence": self.sequence,
               "event_kind": kind, "event_seq": int(event["seq"]),
               "event_recv_ms": finite(event["recv_ms"]),
               "event_source_ms": finite(event.get("source_ts_ms")),
               "source": source, "stage": stage, "reason": reason,
               "connected_after_event": bool(frame["connected"]),
               "candidate_present": candidate is not None,
               "candidate_slot": int(candidate.slot) if candidate is not None else None,
               "candidate_source_ms": finite(candidate.timestamp_s * 1000) if candidate is not None else None,
               "candidate_direction": int(candidate.direction) if candidate is not None else None,
               "market_id": str(market.market_id) if market is not None else None,
               "fair": finite(frame.get("fair")) if reached_fair else None,
               "is_order_attempt": False, "pnl": None, "financial_result": None,
               "no_candidate_internal_reason_known": False,
               "clob_batch_rows": len(event["events"]) if kind == "clob_batch" else None}
        # The sink sees scalar fields only, never the engine frame or objects.
        self.sink(row)

    def reconcile(self, counters, *, eligible_signals=None):
        if self.errors:
            raise ValueError("engine exception prevents a complete successful ledger")
        for key, expected in self.expected_counters.items():
            if counters.get(key, 0) != expected:
                raise ValueError(f"upstream counter mismatch: {key}")
        for key, actual in counters.items():
            if key.startswith("candidate_") and actual != self.expected_counters.get(key, 0):
                raise ValueError(f"unreconciled candidate counter: {key}")
        eligible = sum(n for (stage, _, _), n in self.outcomes.items() if stage == "eligible")
        if eligible_signals is not None and eligible != eligible_signals:
            raise ValueError("eligible-to-HReplay signal count mismatch")
        return {"merged_events": self.sequence,
                "source_events": dict(self.source_events),
                "outcomes": [{"stage": stage, "reason": reason, "source": source, "count": n}
                             for (stage, reason, source), n in sorted(
                                 self.outcomes.items(), key=lambda item: tuple(str(x) for x in item[0]))],
                "upstream_counters_reconciled": True, "eligible_signals": eligible,
                "eligible_signal_join_verified": eligible_signals is not None,
                "complete_merged_event_gate_ledger": True,
                "complete_trigger_internal_rejection_explanation": False,
                "complete_clob_individual_update_rejection_ledger": False,
                "financial_result": None}


def instrument(engine, sink):
    """Compile a separate hash-bound function; do not patch module globals."""
    digest = hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest()
    if digest != EXPECTED_ENGINE_SHA256:
        raise ValueError("upstream engine identity drift")
    original = engine.replay_normalized
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    definition = tree.body[0]
    loops = [node for node in definition.body if isinstance(node, ast.For)
             and isinstance(node.iter, ast.Call)
             and isinstance(node.iter.func, ast.Name) and node.iter.func.id == "_merged_events"]
    if len(loops) != 1 or ast.dump(loops[0].target) != ast.dump(ast.Name(id="event", ctx=ast.Store())):
        raise ValueError("unexpected upstream event loop shape")
    reserved = {"_h_audit_failed", "_h_audit_exit"}
    if any(isinstance(node, ast.Name) and node.id in reserved for node in ast.walk(tree)):
        raise ValueError("upstream observer namespace collision")
    loop = loops[0]
    flag = ast.parse("_h_audit_failed = False").body[0]
    handler = ast.ExceptHandler(type=ast.Name(id="BaseException", ctx=ast.Load()), name=None,
        body=ast.parse("_h_audit_failed = True\nraise").body)
    callback = ast.parse("_h_audit_exit(locals(), _h_audit_failed)").body
    loop.body = [flag, ast.Try(body=loop.body, handlers=[handler], orelse=[], finalbody=callback)]
    ast.fix_missing_locations(tree)
    observer = UpstreamObserver(sink)
    namespace = dict(original.__globals__)
    namespace["_h_audit_exit"] = observer.exit_event
    exec(compile(tree, "<hash-bound-h-upstream-observation>", "exec"), namespace)
    return namespace[original.__name__], observer
