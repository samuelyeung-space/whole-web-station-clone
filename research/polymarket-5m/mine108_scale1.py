"""M500-108 with the previously registered 439 equal-entry policy.

This module registers one NEW development execution variant. It neither
executes orders nor proves scale-in execution, fills, profitability or OOS.
The 108 signal is unchanged. An executor must interpret ``scale_policy``;
importing this module does not add it to the connected replay allowlist.

The policy is copied verbatim from M500-439-SCALE1-RECOVER registration.
It does not assert that re-entry is profitable-only or always same-side:
feedback, entry eligibility, side changes, reservation, FAK failures and
partial fills remain the responsibility of the matching scale-in engine.
No legacy scalein.py implementation is imported or substituted here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import mine500_catalog as _base


VERSION = "mine108-scale1-development-v1"
CANDIDATE_ID = "M500-108-SCALE1"
PARENT_ID = "M500-108"
PARENT_CODE_SHA256 = "23219a5c4ad2320ea68e202f83630d56858513cbe45a2797ee551e9b15aec2c7"
PARENT_RULE_FINGERPRINT = "6075e1b82f627f700e7001d3a1c2d746d28d0b3426f8d93ccd7bae7fb0f9a440"
RESEARCH_STATUS = "development_new_execution_variant_pending_engine/allowlist"
_POLICY_ITEMS = (
    ("cooldown_ms", 2000),
    ("feedback_ms", 250),
    ("max_entries", 99),
    ("mode", "equal"),
    ("total_weight", 99),
)
_PARENT_PARAMS = {
    "tau_min_s": 120.0,
    "tau_max_s": 180.0,
    "tau_upper_inclusive": False,
    "threshold": 1.125,
    "threshold_multiplier": 1.5,
}
_PARENT_FEATURES = ["tau_s", "r_5s", "r_15s"]


def _source_sha256() -> str:
    return hashlib.sha256(Path(_base.__file__).read_bytes()).hexdigest()


def verify_parent_identity() -> dict:
    """Fail closed before registration if the frozen dependency has changed."""
    if _source_sha256() != PARENT_CODE_SHA256:
        raise RuntimeError("M500-108 frozen catalog source SHA-256 mismatch")
    parents = [c for c in _base.make_candidates() if c["id"] == PARENT_ID]
    if len(parents) != 1:
        raise RuntimeError("Expected exactly one frozen M500-108 parent")
    parent = parents[0]
    if (
        parent.get("rule_fingerprint") != PARENT_RULE_FINGERPRINT
        or _base.rule_fingerprint(parent) != PARENT_RULE_FINGERPRINT
        or parent.get("params") != _PARENT_PARAMS
        or parent.get("template") != "nested_5_15"
        or parent.get("required_features") != _PARENT_FEATURES
    ):
        raise RuntimeError("M500-108 frozen parent executable identity mismatch")
    return deepcopy(parent)


def scale_policy() -> dict:
    """Return a fresh copy; callers cannot mutate subsequent registrations."""
    return dict(_POLICY_ITEMS)


def rule_fingerprint() -> str:
    """Bind unchanged parent identity and exact added execution policy."""
    core = {
        "execution_variant_version": VERSION,
        "parent_code_sha256": PARENT_CODE_SHA256,
        "parent_rule_fingerprint": PARENT_RULE_FINGERPRINT,
        "signal_semantics": "unchanged_parent",
        "scale_policy": scale_policy(),
    }
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def make_candidates() -> list[dict]:
    """Register one candidate, retaining original signal parameters verbatim."""
    candidate = verify_parent_identity()
    candidate.update({
        "id": CANDIDATE_ID,
        "version": VERSION,
        "name": candidate["name"] + " / equal-size scale-in",
        "parent_id": PARENT_ID,
        "parent_code_sha256": PARENT_CODE_SHA256,
        "parent_rule_fingerprint": PARENT_RULE_FINGERPRINT,
        "scale_policy": scale_policy(),
        "rule_fingerprint": rule_fingerprint(),
        "research_status": RESEARCH_STATUS,
        "evidence_classification": "historical_development_new_execution_variant",
        "independent_mechanism_claim": False,
        "confirmed_edge": False,
        "full_window_pnl_usd": None,
        "formal_oos_result": None,
    })
    return [candidate]


def catalog() -> list[dict]:
    """Alias for loaders that call catalog() rather than make_candidates()."""
    return make_candidates()


def _candidate_matches(candidate: dict) -> bool:
    if not isinstance(candidate, dict):
        return False
    expected = {
        "id": CANDIDATE_ID,
        "version": VERSION,
        "parent_id": PARENT_ID,
        "parent_code_sha256": PARENT_CODE_SHA256,
        "parent_rule_fingerprint": PARENT_RULE_FINGERPRINT,
        "rule_fingerprint": rule_fingerprint(),
        "template": "nested_5_15",
        "params": _PARENT_PARAMS,
        "required_features": _PARENT_FEATURES,
        "execution_kind": "single_leg_taker_research",
        "scale_policy": scale_policy(),
    }
    if any(candidate.get(key) != value for key, value in expected.items()):
        return False
    # Reject bool/float aliases for integer execution controls.
    policy = candidate["scale_policy"]
    return all(type(policy[key]) is int for key in (
        "cooldown_ms", "feedback_ms", "max_entries", "total_weight"
    )) and type(candidate["params"]["tau_upper_inclusive"]) is bool


_PARENT = verify_parent_identity()


def signal(candidate: dict, features: dict) -> str | None:
    """Delegate to frozen 108; altered signal/policy specifications fail closed.

    Required features must already be causally constructed from local receipts.
    This function has no state, position sizing, fill simulation or cash model.
    """
    if not _candidate_matches(candidate):
        return None
    return _base.signal(_PARENT, features)


if __name__ == "__main__":
    print(json.dumps({"schema": VERSION, "candidates": catalog()}, sort_keys=True, indent=2))
