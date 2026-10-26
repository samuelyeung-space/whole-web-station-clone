"""Bounded M500-108 trend-filter / cheaper-entry DEVELOPMENT ablations.

The original signal is retained as REFERENCE. Other arms share causal context
readiness, then consume their FIRST qualifying 108 signal even when a regime
filter rejects it. WAIT arms latch that direction before watching a weak
countermove; instantaneous 108 conditions are deliberately not required at
the later confirmation. This module does not infer a large trader's intent.

No adds or execution implementation exist here. The caller must use the
existing receipt-causal FAK/cash/fee engine, independent cash per arm/latency,
and retain failed/partial orders and no-entry arm outcomes separately.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from numbers import Real

VERSION = "trend-pullback-ablation-development-v1"
PARENT_RULE_FINGERPRINT = "6075e1b82f627f700e7001d3a1c2d746d28d0b3426f8d93ccd7bae7fb0f9a440"
PARAMS = {
    "tau_min_s": 120, "tau_max_s_exclusive": 180,
    "r5_threshold_bps": 1.125, "wait_ms_exclusive": 10000,
    "max_pullback_fraction_of_armed_abs_r15": 0.5,
    "required_direct_ask_discount_usd": 0.01,
    "trend_bar_seconds": 60, "ma_length": 10,
    "supertrend_atr_length": 10, "supertrend_multiplier": 3,
}
_DEFS = (
    ("REFERENCE", (), False),
    ("CONTROL", (), False),
    ("MA10", ("ma",), False),
    ("ST10", ("st",), False),
    ("WAIT", (), True),
    ("MAWAIT", ("ma",), True),
    ("STWAIT", ("st",), True),
    ("BOTHWAIT", ("ma", "st"), True),
)
_BASE_FEATURES = ["tau_s", "r_5s", "r_15s"]
_CTX_FEATURES = [
    "ctx_ma_direction", "ctx_st_direction", "ctx_price", "ctx_r1_bps",
    "ctx_epoch", "ctx_market_start_ms", "ctx_decision_ms",
    "ctx_ask_up", "ctx_ask_down",
]


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def catalog():
    out = []
    for arm, filters, waits in _DEFS:
        core = {"version": VERSION, "parent_rule_fingerprint": PARENT_RULE_FINGERPRINT,
                "arm": arm, "filters": list(filters), "waits": waits, "params": PARAMS}
        out.append({
            "id": "TPB108-" + arm, "version": VERSION, "arm": arm,
            "name": "M500-108 / " + arm,
            "parent_rule_fingerprint": PARENT_RULE_FINGERPRINT,
            "params": deepcopy(PARAMS), "filters": list(filters), "waits": waits,
            "required_features": _BASE_FEATURES + ([] if arm == "REFERENCE" else _CTX_FEATURES),
            "rule_fingerprint": _hash(core),
            "research_status": "registered_development_ablation_not_executed",
            "independent_mechanism_claim": False, "formal_oos_result": None,
        })
    return out


make_candidates = catalog
_EXPECTED = {row["id"]: row for row in catalog()}


def parent_direction(features):
    """Exact numerical 108 rule, in basis points and seconds remaining."""
    values = [features.get(k) for k in _BASE_FEATURES]
    if not all(_finite(x) for x in values):
        return 0
    tau, r5, r15 = values
    if 120 <= tau < 180 and abs(r5) >= 1.125 and r5 * r15 > 0:
        return 1 if r5 > 0 else -1
    return 0


class SignalMachine:
    """One independent state per candidate and five-minute market start."""
    def __init__(self):
        self.states = {}
        self.events = []

    def _record(self, cid, key, state, status, now, **extra):
        state["status"] = status
        self.events.append({"candidate_id": cid, "market_start_ms": key,
                            "decision_ms": now, "status": status, **extra})

    @staticmethod
    def _regime(candidate, features, side):
        return all(features["ctx_" + name + "_direction"] == side
                   for name in candidate["filters"])

    def signal(self, candidate, features):
        cid = candidate.get("id") if isinstance(candidate, dict) else None
        expected = _EXPECTED.get(cid)
        if expected is None or any(candidate.get(k) != expected[k] for k in
                ("version", "arm", "params", "filters", "waits", "required_features",
                 "rule_fingerprint", "parent_rule_fingerprint")):
            return None
        if not isinstance(features, dict):
            return None
        side = parent_direction(features)
        if candidate["arm"] == "REFERENCE":
            return "Up" if side == 1 else "Down" if side == -1 else None
        if not all(_finite(features.get(name)) for name in candidate["required_features"]):
            return None
        if (features["ctx_price"] <= 0 or features["ctx_st_direction"] not in (-1, 1)
                or features["ctx_ma_direction"] not in (-1, 0, 1)):
            return None
        key = features["ctx_market_start_ms"]
        now = features["ctx_decision_ms"]
        state = self.states.get((cid, key))
        if state is not None:
            if state["status"] not in ("armed", "pullback_seen"):
                return None
            if now <= state["last_decision_ms"]:
                self._record(cid, key, state, "cancel_nonincreasing_decision", now)
                return None
            state["last_decision_ms"] = now
            if features["ctx_epoch"] != state["epoch"]:
                self._record(cid, key, state, "cancel_epoch_change", now)
                return None
            if now - state["arm_ms"] >= PARAMS["wait_ms_exclusive"] or features["tau_s"] < 120:
                self._record(cid, key, state, "timeout", now)
                return None
            if not self._regime(candidate, features, state["side"]):
                self._record(cid, key, state, "cancel_regime_change", now)
                return None
            retracement = state["side"] * 10000 * math.log(state["arm_price"] / features["ctx_price"])
            if retracement > state["max_pullback_bps"] + 1e-12:
                self._record(cid, key, state, "cancel_overdeep", now, retracement_bps=retracement)
                return None
            if state["status"] == "armed":
                if retracement > 0 and state["side"] * features["r_5s"] < 0:
                    state["pullback_ms"] = now
                    self._record(cid, key, state, "pullback_seen", now,
                                 retracement_bps=retracement)
                return None
            ask = features["ctx_ask_up"] if state["side"] == 1 else features["ctx_ask_down"]
            if (now > state["pullback_ms"] and 0 < retracement <= state["max_pullback_bps"] + 1e-12
                    and state["side"] * features["ctx_r1_bps"] > 0
                    and state["side"] * features["r_15s"] > 0
                    and 0 < ask <= state["arm_ask"] - PARAMS["required_direct_ask_discount_usd"] + 1e-12):
                self._record(cid, key, state, "confirmed", now, entry_ask=ask,
                             arm_ask=state["arm_ask"], retracement_bps=retracement)
                return "Up" if state["side"] == 1 else "Down"
            return None
        if not side:
            return None
        # First raw 108 signal with common context readiness is immutable.
        state = {"status": "new", "side": side, "arm_ms": now,
                 "last_decision_ms": now, "epoch": features["ctx_epoch"]}
        self.states[(cid, key)] = state
        if not self._regime(candidate, features, side):
            self._record(cid, key, state, "filtered_first_signal", now)
            return None
        if not candidate["waits"]:
            self._record(cid, key, state, "immediate", now)
            return "Up" if side == 1 else "Down"
        ask = features["ctx_ask_up"] if side == 1 else features["ctx_ask_down"]
        if not 0 < ask < 1:
            self._record(cid, key, state, "bad_reference_quote", now)
            return None
        state.update(arm_price=features["ctx_price"], arm_ask=ask,
                     max_pullback_bps=0.5 * abs(features["r_15s"]))
        self._record(cid, key, state, "armed", now, arm_ask=ask,
                     max_pullback_bps=state["max_pullback_bps"])
        return None

    def audit(self):
        return {"events": deepcopy(self.events), "states": [
            {"candidate_id": cid, "market_start_ms": key, **deepcopy(state)}
            for (cid, key), state in self.states.items()],
            "event_counts": dict(Counter(e["status"] for e in self.events)),
            "execution_orders_not_implemented_here": True}


_MACHINE = SignalMachine()


def reset():
    global _MACHINE
    _MACHINE = SignalMachine()


def signal(candidate, features):
    return _MACHINE.signal(candidate, features)


def audit():
    return _MACHINE.audit()

