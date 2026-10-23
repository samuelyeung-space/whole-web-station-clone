"""Hash-bound scalar ledger of each CLOB update inside the original batch loop.

This observes the original apply/reject decisions and post-update readiness;
it does not repair books, infer queue fills or inspect future health.
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
CONTINUE_REASONS = ("disconnect_reset", "reconnect", "disconnected", "unknown_asset")


def finite(value):
    if value is None: return None
    value = float(value)
    return value if math.isfinite(value) else None


def book_state(token):
    if token is None: return {}
    return {"ready": bool(token.ready), "source_ms": finite(token.source_ms),
            "bid_version": int(token.bid_version), "ask_version": int(token.ask_version),
            "bid_levels": len(token.bids), "ask_levels": len(token.asks)}


class ClobUpdateObserver:
    def __init__(self):
        self.before = None
        self.pending = []
        self.sequence = 0
        self.batches = 0
        self.joined_rows = 0
        self.errors = 0
        self.outcomes = Counter()

    def start(self, event, token_index, connected):
        if self.before is not None:
            raise ValueError("unclosed individual CLOB observation")
        hit = token_index.get(str(event.get("asset_id")))
        token = (hit[0].up if hit[1] == "Up" else hit[0].down) if hit else None
        self.before = {"connected": bool(connected), "token": book_state(token),
                       "market_id": str(hit[0].market_id) if hit else None,
                       "direction": hit[1] if hit else None}

    def exit(self, frame, failed):
        event = frame["event"]; before = self.before
        if before is None:
            raise ValueError("missing individual CLOB entry")
        reason = frame["_clob_audit_reason"]
        hit = frame['token_index'].get(str(event.get('asset_id')))
        current = (hit[0].up if hit[1] == 'Up' else hit[0].down) if hit else None
        applied = False; after = book_state(current)
        if failed:
            self.errors += 1; reason = "engine_exception"
        elif reason is None:
            token = frame["token"]; after = book_state(token)
            applied = bool(frame["applied"])
            if event["kind"] == "clob_snapshot":
                reason = "snapshot_applied" if applied else "snapshot_source_regressed"
            elif event["kind"] == "clob_price_change":
                if applied:
                    reason = "delta_applied_ready" if token.ready else "delta_applied_bbo_invalidated"
                elif not before["token"]["ready"]:
                    reason = "delta_before_ready_snapshot"
                else:
                    reason = "delta_source_regressed"
            else:
                reason = "unhandled_kind"
        self.sequence += 1; self.outcomes[reason] += 1
        self.pending.append({"schema": "h-clob-update-v1", "kind": "clob_update_gate",
            "audit_clob_update_sequence": self.sequence,
            "event_kind": event["kind"], "event_seq": int(event["seq"]),
            "event_recv_ms": finite(event["recv_ms"]),
            "event_source_ms": finite(event.get("source_ts_ms")),
            "asset_id": str(event["asset_id"]) if event.get("asset_id") is not None else None,
            "market_id": before["market_id"], "direction": before["direction"],
            "reason": reason, "applied": applied, "connected_before": before["connected"],
            "connected_after": bool(frame["connected"]),
            "ready_before": before["token"].get("ready"), "ready_after": after.get("ready"),
            "token_source_before_ms": before["token"].get("source_ms"),
            "token_source_after_ms": after.get("source_ms"),
            "bid_version_before": before["token"].get("bid_version"),
            "bid_version_after": after.get("bid_version"),
            "ask_version_before": before["token"].get("ask_version"),
            "ask_version_after": after.get("ask_version"),
            "bid_levels_before": before["token"].get("bid_levels"),
            "bid_levels_after": after.get("bid_levels"),
            "ask_levels_before": before["token"].get("ask_levels"),
            "ask_levels_after": after.get("ask_levels"),
            "is_order_attempt": False, "pnl": None, "financial_result": None})
        self.before = None

    def join_batch(self, upstream):
        if upstream["event_kind"] != "clob_batch":
            if self.pending:
                raise ValueError("CLOB updates leaked into a future source event")
            return []
        if self.before is not None or len(self.pending) != upstream["clob_batch_rows"]:
            raise ValueError("CLOB batch/individual count mismatch")
        if not self.pending or self.pending[0]["event_seq"] != upstream["event_seq"]:
            raise ValueError("CLOB batch first sequence mismatch")
        rows = self.pending
        for row in rows:
            if row["event_recv_ms"] != upstream["event_recv_ms"]:
                raise ValueError("CLOB batch receive clock mismatch")
            row["audit_event_sequence"] = upstream["audit_event_sequence"]
        self.pending = []; self.batches += 1; self.joined_rows += len(rows)
        return rows

    def reconcile(self, raw_clob_rows, merged_clob_batches):
        if self.errors or self.before is not None or self.pending:
            raise ValueError("incomplete/error CLOB ledger")
        if self.sequence != raw_clob_rows or self.joined_rows != raw_clob_rows or self.batches != merged_clob_batches:
            raise ValueError("CLOB raw/merged coverage mismatch")
        return {"individual_clob_rows": self.sequence, "merged_clob_batches": self.batches,
                "outcomes": dict(self.outcomes), "all_individual_updates_joined_exactly_once": True,
                "complete_normalized_clob_update_outcome_ledger": True,
                "pre_normalization_parser_coverage_complete": False,
                "maker_queue_or_real_fills_verified": False, "financial_result": None}


def instrument(engine):
    if hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest() != EXPECTED_ENGINE_SHA256:
        raise ValueError("CLOB engine identity drift")
    original = engine._apply_clob_batch
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    loops = [n for n in tree.body[0].body if isinstance(n, ast.For)
             and isinstance(n.iter, ast.Subscript) and ast.unparse(n.iter) == "batch['events']"]
    if len(loops) != 1 or ast.unparse(loops[0].target) != "event":
        raise ValueError("unexpected CLOB loop shape")
    loop = loops[0]
    continues = sorted((n for n in ast.walk(loop) if isinstance(n, ast.Continue)), key=lambda n:n.lineno)
    if len(continues) != len(CONTINUE_REASONS):
        raise ValueError("unexpected CLOB continue shape")
    reserved = {"_clob_audit_start", "_clob_audit_exit", "_clob_audit_reason", "_clob_audit_failed"}
    if any(isinstance(n, ast.Name) and n.id in reserved for n in ast.walk(tree)):
        raise ValueError("CLOB observer namespace collision")
    reasons = {id(n):r for n,r in zip(continues, CONTINUE_REASONS)}
    class Mark(ast.NodeTransformer):
        def visit_Continue(self, node):
            return [ast.Assign(targets=[ast.Name(id="_clob_audit_reason",ctx=ast.Store())],
                               value=ast.Constant(reasons[id(node)])), node]
    Mark().visit(loop)
    start = ast.parse("_clob_audit_reason = None\n_clob_audit_failed = False\n_clob_audit_start(event, token_index, connected)").body
    handler = ast.ExceptHandler(type=ast.Name(id="BaseException",ctx=ast.Load()),name=None,
                               body=ast.parse("_clob_audit_failed = True\nraise").body)
    loop.body = start + [ast.Try(body=loop.body,handlers=[handler],orelse=[],
                                finalbody=ast.parse("_clob_audit_exit(locals(), _clob_audit_failed)").body)]
    ast.fix_missing_locations(tree)
    observer = ClobUpdateObserver(); namespace = dict(original.__globals__)
    namespace.update(_clob_audit_start=observer.start,_clob_audit_exit=observer.exit)
    exec(compile(tree,"<hash-bound-clob-update-observation>","exec"),namespace)
    return namespace[original.__name__], observer
