"""Fail-closed checks for reusing fitted development probabilities unchanged."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import mine500_endpoint_model as original
import mine500_fixed100 as fixed
import mine500_probability_models as prior


class Fixed100Guards(unittest.TestCase):
    def test_changed_entry_margin_cannot_inherit_selected_rule(self):
        registration = {"candidates": original.candidates()}
        registration["candidates"][0]["margin"] = .01
        with self.assertRaisesRegex(ValueError, "differs"):
            fixed.selected_candidate(registration)

    def score_fixture(self):
        cutoff = prior._start("2026-10-08")
        fold = dict(model_id=fixed.MODEL_ID, status="scored", day="2026-10-08",
                    train_hash="synthetic-training", learned_state=[], cutoff_ms=cutoff,
                    latest_training_label_ms=cutoff-1, training_market_ids=["training-market"])
        fold["model_hash"] = prior._hash([fixed.MODEL_ID, fold["train_hash"], fold["learned_state"]])
        row = dict(model_id=fixed.MODEL_ID, market_id="new-market", recv_ms=cutoff+1000,
                   model_hash=fold["model_hash"], model_fit_ms=cutoff,
                   feature_available_ms=cutoff+1000, p_up=.61)
        return {"folds": [fold]}, row

    def test_future_fitted_score_is_rejected(self):
        audit, row = self.score_fixture()
        row["feature_available_ms"] = row["recv_ms"] + 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"scores.jsonl"
            path.write_text(json.dumps(row)+"\n")
            with self.assertRaisesRegex(ValueError, "future model"):
                fixed.checked_scores(path, audit)

    def test_training_market_score_is_rejected(self):
        audit, row = self.score_fixture()
        row["market_id"] = "training-market"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"scores.jsonl"
            path.write_text(json.dumps(row)+"\n")
            with self.assertRaisesRegex(ValueError, "training market"):
                fixed.checked_scores(path, audit)

    def test_tape_probability_edit_is_rejected(self):
        _, row = self.score_fixture()
        scores = {(row["market_id"], row["recv_ms"]): row}
        tape = [dict(type="dataset", split="development", observed_intervals_ms=[[row["recv_ms"],row["recv_ms"]+1000]]),
                dict(type="decision", market_id=row["market_id"], recv_ms=row["recv_ms"],
                     features={fixed.SCORE_KEY: .62},
                     feature_available_ms={fixed.SCORE_KEY: row["feature_available_ms"]})]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"tape.jsonl"
            path.write_text("".join(json.dumps(r)+"\n" for r in tape))
            with self.assertRaisesRegex(ValueError, "differs from original"):
                fixed.validate_enriched(path, scores, ["2026-10-08"])


if __name__ == "__main__":
    unittest.main()
