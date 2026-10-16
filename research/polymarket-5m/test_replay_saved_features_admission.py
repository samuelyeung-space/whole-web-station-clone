"""Adversarial synthetic admission checks, not market backtests or PnL tests."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import replay_saved_features_admission as gate


class SavedFeaturesAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = gate.DAY_MS
        self.endpoint = self.root / "endpoint.gz"
        self.scores = self.root / "scores.gz"
        self.registration = self.root / "registration.json"
        self.audit = self.root / "audit.json"
        self.events = [
            {"type": "dataset", "recv_ms": self.base, "dataset_id": "synthetic",
             "split": "development"},
            {"type": "decision", "market_id": "m", "recv_ms": self.base + 1000,
             "features": {"tau_s": 100, "imbalance_up": .2},
             "feature_available_ms": {"tau_s": self.base+1000, "imbalance_up": self.base+999}},
            {"type": "decision", "market_id": "m", "recv_ms": self.base + 2000,
             "features": {"tau_s": 99, "imbalance_up": None},
             "feature_available_ms": {"tau_s": self.base+2000, "imbalance_up": None}},
        ]
        self.score_rows = [{"market_id": "m", "recv_ms": self.base+1000,
                            "scores": {"book_5": .7, "r5": -.3}}]
        self.audit_rows = [{"day": 0, "status": "warmup_no_fit"},
                           {"day": 1, "model": "book_5", "latest_label_ms": self.base-1}]
        self.write_gzip(self.scores, self.score_rows)
        self.write_endpoint()
        self.write_audit()

    def tearDown(self):
        self.tmp.cleanup()

    def write_gzip(self, path, rows):
        with gzip.open(path, "wt") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def write_endpoint(self):
        self.write_gzip(self.endpoint, self.events)
        self.registration.write_text(json.dumps({
            "input_sha256": {"endpoint": gate.sha256(self.endpoint)},
            "candidates": [{"id": "SYNTHETIC", "model": "book_5"}],
        }))

    def write_audit(self):
        self.audit.write_text(json.dumps(self.audit_rows))

    def admit(self, **kwargs):
        return gate.admit_saved_features([self.endpoint], self.scores, self.registration,
            self.audit, required_aliases={"imbalance": "imbalance_up"}, **kwargs)

    def test_valid_input_preserves_bytes_and_reports_missing_score_and_alias(self):
        before = {p: p.read_bytes() for p in [self.endpoint, self.scores, self.registration, self.audit]}
        scores, evidence = self.admit(expected_scores_sha256=gate.sha256(self.scores))
        self.assertEqual(scores[("m", self.base+1000)], self.score_rows[0]["scores"])
        self.assertEqual(evidence["decisions"], 2)
        self.assertEqual(evidence["matched_score_rows"], 1)
        self.assertEqual(evidence["decisions_without_score"], 1)
        self.assertEqual(evidence["inputs"][0]["alias_coverage"],
                         {"imbalance:available_source": 1, "imbalance:missing_source": 1})
        self.assertEqual(evidence["scores_sha256"], gate.sha256(self.scores))
        self.assertIsNone(evidence["formal_oos_result"])
        self.assertFalse(evidence["model_fitting"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unregistered_endpoint_bytes_rejected(self):
        with gzip.open(self.endpoint, "at") as f:
            f.write('{}\n')
        with self.assertRaisesRegex(ValueError, "original model registration"):
            self.admit()

    def test_registered_holdout_endpoint_rejected(self):
        self.events[0]["split"] = "holdout"
        self.write_endpoint()
        with self.assertRaisesRegex(ValueError, "development"):
            self.admit()

    def test_duplicate_score_key_rejected(self):
        self.write_gzip(self.scores, self.score_rows * 2)
        with self.assertRaisesRegex(ValueError, "duplicate score key"):
            self.admit()

    def test_nonfinite_boolean_and_nonnumeric_score_values_rejected(self):
        for bad in [float("nan"), float("inf"), True, None, "0.7"]:
            with self.subTest(value=repr(bad)):
                self.score_rows[0]["scores"]["book_5"] = bad
                self.write_gzip(self.scores, self.score_rows)
                with self.assertRaises(ValueError):
                    self.admit()

    def test_model_probability_range_rejected(self):
        for bad in [-.01, 1.01]:
            with self.subTest(value=bad):
                self.score_rows[0]["scores"]["book_5"] = bad
                self.write_gzip(self.scores, self.score_rows)
                with self.assertRaisesRegex(ValueError, "probability outside"):
                    self.admit()

    def test_score_model_without_exact_day_cutoff_rejected(self):
        self.audit_rows[1]["day"] = 2
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "matching earlier-day"):
            self.admit()

    def test_score_without_model_rejected(self):
        self.score_rows[0]["scores"] = {"r5": .5}
        self.write_gzip(self.scores, self.score_rows)
        with self.assertRaisesRegex(ValueError, "without registered model"):
            self.admit()

    def test_unknown_model_is_not_treated_as_plain_feature(self):
        self.score_rows[0]["scores"]["book_60"] = .6
        self.write_gzip(self.scores, self.score_rows)
        with self.assertRaisesRegex(ValueError, "unregistered score model"):
            self.admit()

    def test_cutoff_on_or_after_prediction_day_rejected(self):
        for bad in [self.base, self.base+1]:
            with self.subTest(value=bad):
                self.audit_rows[1]["latest_label_ms"] = bad
                self.write_audit()
                with self.assertRaisesRegex(ValueError, "strictly earlier UTC day"):
                    self.admit()

    def test_duplicate_audit_key_rejected_even_when_values_equal(self):
        self.audit_rows.append(dict(self.audit_rows[1]))
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "duplicate model audit key"):
            self.admit()

    def test_nonfinite_audit_cutoff_rejected(self):
        self.audit_rows[1]["latest_label_ms"] = float("nan")
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.admit()

    def test_saved_score_cannot_overwrite_existing_different_feature(self):
        self.events[1]["features"]["book_5"] = .9
        self.events[1]["feature_available_ms"]["book_5"] = self.base+1000
        self.write_endpoint()
        with self.assertRaisesRegex(ValueError, "conflicts with original feature"):
            self.admit()

    def test_equal_score_cannot_launder_future_original_availability(self):
        self.events[1]["features"]["book_5"] = .7
        self.events[1]["feature_available_ms"]["book_5"] = self.base+1001
        self.write_endpoint()
        with self.assertRaisesRegex(ValueError, "overlapping saved score feature unavailable"):
            self.admit()

    def test_mar_alias_future_or_unknown_availability_rejected(self):
        for bad in [self.base+1001, None, True]:
            with self.subTest(value=bad):
                self.events[1]["feature_available_ms"]["imbalance_up"] = bad
                self.write_endpoint()
                with self.assertRaisesRegex(ValueError, "required alias source feature unavailable"):
                    self.admit()

    def test_mar_alias_cannot_replace_conflicting_destination(self):
        self.events[1]["features"]["imbalance"] = -.2
        self.events[1]["feature_available_ms"]["imbalance"] = self.base+1000
        self.write_endpoint()
        with self.assertRaisesRegex(ValueError, "derived alias conflicts"):
            self.admit()

    def test_expected_original_score_hash_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "expected original hash"):
            self.admit(expected_scores_sha256="0"*64)

    def test_duplicate_json_feature_key_rejected(self):
        with gzip.open(self.scores, "wt") as f:
            f.write('{"market_id":"m","recv_ms":86401000,"scores":{"book_5":0.7,"book_5":0.8}}\n')
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            self.admit()

    def test_duplicate_decisions_rejected_instead_of_double_counted(self):
        self.events.append(dict(self.events[1]))
        self.write_endpoint()
        with self.assertRaisesRegex(ValueError, "duplicate decision key"):
            self.admit()

    def test_missing_dataset_header_rejected_even_when_hash_registered(self):
        self.events.pop(0)
        self.write_endpoint()
        with self.assertRaisesRegex(ValueError, "begin with dataset"):
            self.admit()

    def test_truncated_gzip_rejected_after_hash_registration(self):
        self.endpoint.write_bytes(self.endpoint.read_bytes()[:-8])
        reg = json.loads(self.registration.read_text())
        reg["input_sha256"]["endpoint"] = gate.sha256(self.endpoint)
        self.registration.write_text(json.dumps(reg))
        with self.assertRaisesRegex(ValueError, "incomplete or corrupt"):
            self.admit()


if __name__ == "__main__":
    unittest.main()
