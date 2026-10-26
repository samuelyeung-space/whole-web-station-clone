"""Signal/registration checks only; these are NOT scale-in execution tests."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import mine108_scale1 as variant
import mine500_catalog as base


class SignalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.candidate = variant.make_candidates()[0]
        self.features = {"tau_s": 150.0, "r_5s": 1.125, "r_15s": 2.0}

    def signal(self, **changes):
        return variant.signal(self.candidate, self.features | changes)

    def test_tau_boundaries(self):
        self.assertEqual(self.signal(tau_s=120.0), "Up")
        self.assertEqual(self.signal(tau_s=179.999), "Up")
        self.assertIsNone(self.signal(tau_s=119.999))
        self.assertIsNone(self.signal(tau_s=180.0))

    def test_threshold_is_108_not_107(self):
        self.assertEqual(self.signal(r_5s=1.125), "Up")
        self.assertIsNone(self.signal(r_5s=1.124999))
        self.assertIsNone(self.signal(r_5s=0.75))
        self.assertEqual(self.signal(r_5s=-1.125, r_15s=-2), "Down")

    def test_sign_agreement_is_required(self):
        self.assertIsNone(self.signal(r_15s=-2))
        self.assertIsNone(self.signal(r_15s=0))
        self.assertIsNone(self.signal(r_5s=0))

    def test_missing_and_invalid_features_fail_closed(self):
        missing = self.features.copy()
        del missing["r_15s"]
        self.assertIsNone(variant.signal(self.candidate, missing))
        for value in (None, True, float("nan"), float("inf"), "2"):
            with self.subTest(value=value):
                self.assertIsNone(self.signal(r_15s=value))

    def test_signal_matches_parent_and_does_not_mutate_inputs(self):
        parent = variant.verify_parent_identity()
        candidate_before = deepcopy(self.candidate)
        features_before = deepcopy(self.features)
        examples = [
            self.features,
            self.features | {"r_5s": -2, "r_15s": -3},
            self.features | {"r_15s": -3},
            self.features | {"tau_s": 180},
        ]
        for features in examples:
            self.assertEqual(variant.signal(self.candidate, features), base.signal(parent, features))
        self.assertEqual(self.candidate, candidate_before)
        self.assertEqual(self.features, features_before)


class RegistrationIdentityTests(unittest.TestCase):
    def test_parent_sha_and_fingerprint(self):
        parent = variant.verify_parent_identity()
        self.assertEqual(parent["rule_fingerprint"], variant.PARENT_RULE_FINGERPRINT)
        self.assertEqual(variant._source_sha256(), variant.PARENT_CODE_SHA256)
        with patch.object(variant, "_source_sha256", return_value="0" * 64):
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                variant.verify_parent_identity()

    def test_policy_exactly_matches_registered_439(self):
        self.assertEqual(variant.scale_policy(), {
            "cooldown_ms": 2000, "feedback_ms": 250, "max_entries": 99,
            "mode": "equal", "total_weight": 99,
        })

    def test_fingerprint_is_deterministic_and_binds_parent_and_policy(self):
        expected_core = {
            "execution_variant_version": variant.VERSION,
            "parent_code_sha256": variant.PARENT_CODE_SHA256,
            "parent_rule_fingerprint": variant.PARENT_RULE_FINGERPRINT,
            "signal_semantics": "unchanged_parent",
            "scale_policy": variant.scale_policy(),
        }
        expected = hashlib.sha256(json.dumps(
            expected_core, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()).hexdigest()
        self.assertEqual(variant.rule_fingerprint(), expected)
        self.assertNotEqual(expected, variant.PARENT_RULE_FINGERPRINT)
        self.assertEqual(variant.catalog(), variant.make_candidates())

    def test_fresh_copies_leave_frozen_base_unchanged(self):
        parent_before = base.make_candidates()[107]
        candidate = variant.make_candidates()[0]
        candidate["params"]["threshold"] = 0.75
        candidate["scale_policy"]["max_entries"] = 1
        candidate["required_features"].clear()
        fresh = variant.make_candidates()[0]
        self.assertEqual(fresh["params"], parent_before["params"])
        self.assertEqual(fresh["scale_policy"]["max_entries"], 99)
        self.assertEqual(fresh["required_features"], parent_before["required_features"])
        self.assertEqual(base.make_candidates()[107], parent_before)

    def test_tampered_signal_and_execution_parameters_are_rejected(self):
        features = {"tau_s": 150, "r_5s": 2, "r_15s": 2}
        changes = [
            ("id", "M500-107-SCALE1"),
            ("rule_fingerprint", "0" * 64),
            ("parent_code_sha256", "0" * 64),
            ("params", variant._PARENT_PARAMS | {"threshold": 0.75}),
            ("scale_policy", variant.scale_policy() | {"cooldown_ms": 0}),
            ("scale_policy", variant.scale_policy() | {"max_entries": 99.0}),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                candidate = variant.make_candidates()[0]
                candidate[key] = value
                self.assertIsNone(variant.signal(candidate, features))

    def test_candidate_has_no_performance_or_independent_edge_claim(self):
        candidate = variant.catalog()[0]
        self.assertEqual(candidate["research_status"], variant.RESEARCH_STATUS)
        self.assertIsNone(candidate["full_window_pnl_usd"])
        self.assertIsNone(candidate["formal_oos_result"])
        self.assertFalse(candidate["independent_mechanism_claim"])
        self.assertFalse(candidate["confirmed_edge"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
