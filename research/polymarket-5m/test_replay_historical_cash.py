"""Two-tape capital counterexamples; synthetic fixtures are not backtests."""
import gzip
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import replay_dataset as ds
import replay_engine as ex
import replay_runner as rr


DAY = 86400000


class HistoricalCashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.module = "historical_cash_" + uuid.uuid4().hex
        (self.root / (self.module + ".py")).write_text(
            "def catalog():\n    return [{'id':'UP','required_features':[]}]\n"
            "def signal(c,f):\n    return 'Up'\n")
        sys.path.insert(0, str(self.root))
        importlib.invalidate_caches()

    def tearDown(self):
        sys.path.remove(str(self.root))
        sys.modules.pop(self.module, None)
        self.temp.cleanup()

    def tape(self, day, outcome_ms=None, assumed=False):
        mid = "m" + str(day)
        header = {"type": "dataset", "recv_ms": day, "dataset_id": mid,
                  "split": "development", "provenance": {"source": "synthetic-test"},
                  "coverage_start_ms": day + 100, "coverage_end_ms": day + 1000,
                  "execution_book_sides": ["Up"]}
        if assumed:
            header.update(strict_execution_admitted=False, metadata_availability_assumed=True,
                          assumptions={"settlement_cutoff_ms": outcome_ms},
                          limitations=["Synthetic retrospective metadata assumption"])
        rows = [header,
                {"type": "market", "recv_ms": day, "market_id": mid,
                 "start_ms": day, "end_ms": day + 300000, "label_source": "gamma"},
                {"type": "book", "recv_ms": day + 100, "market_id": mid,
                 "up_recv_ms": day + 100, "up_asks": [[.4, 100]], "up_healthy": True,
                 "healthy": False, "fee_rate": .07},
                {"type": "decision", "recv_ms": day + 100, "market_id": mid,
                 "features": {"tau_s": 299.9}, "feature_available_ms": {"tau_s": day + 100}}]
        if outcome_ms is not None:
            rows.append({"type": "outcome", "recv_ms": outcome_ms, "market_id": mid,
                         "resolved_ms": day + 300000, "winner": "Up", "source": "gamma"})
        path = self.root / (mid + ".jsonl.gz")
        with gzip.open(path, "wt") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def merged_replay(self, first_outcome_ms=None, assumed=False):
        # Intentionally supply the later tape first: admission must merge event
        # clocks, not concatenate files or reset portfolio cash at file borders.
        late = self.tape(DAY, 2 * DAY, assumed)
        early = self.tape(0, first_outcome_ms, assumed)
        cache = ds.ingest([late, early], self.root / "cache")
        config = ex.ReplayConfig(side_specific_books=True, latencies_ms=(500,),
                                 max_quote_age_ms=1000, initial_cash_usd=3, shares=5)
        return rr.execute(ex.read_jsonl(Path(cache["cache_path"]) / "events.jsonl.gz"),
                          [self.module + ":*"], config, mode="shared")

    def test_unresolved_inventory_occupies_cash_across_tape_boundary(self):
        result = self.merged_replay()
        self.assertEqual([row["market_id"] for row in result["attempts"]], ["m0", "m86400000"])
        self.assertEqual([row["status"] for row in result["attempts"]],
                         ["filled_full", "rejected_insufficient_available_cash"])
        row = result["summary"][0]
        self.assertEqual(row["unresolved_positions"], 1)
        self.assertEqual(row["filled_shares"], 5)
        self.assertAlmostEqual(row["ending_cash_usd"], .916)
        self.assertIsNone(row["net_pnl_usd"])
        self.assertTrue(all(event["cash_usd"] - event["reserved_usd"] >= 0 for event in result["cash_events"]))

    def test_late_global_settlement_cannot_finance_an_earlier_next_day_order(self):
        result = self.merged_replay(first_outcome_ms=DAY + 800, assumed=True)
        self.assertEqual(result["attempts"][1]["status"], "rejected_insufficient_available_cash")
        self.assertAlmostEqual(result["summary"][0]["ending_cash_usd"], 5.916)
        self.assertEqual(result["attempts"][0]["settlement_ms"], DAY + 800)
        self.assertEqual(result["evidence_lane"], "limited_historical_sensitivity_not_strict_execution")
        inherited = {row.get("dataset_id") for row in result["inherited_execution_assumptions"]}
        self.assertTrue({"m0", "m86400000"}.issubset(inherited))
        self.assertIn("Synthetic retrospective metadata assumption", result["limitations"])
        self.assertFalse(any("locally known official outcome" in text for text in result["limitations"]))

    def test_verified_prior_outcome_releases_cash_before_later_tape(self):
        result = self.merged_replay(first_outcome_ms=300000)
        self.assertEqual([row["status"] for row in result["attempts"]], ["filled_full", "filled_full"])
        self.assertAlmostEqual(result["attempts"][1]["cash_before_decision"], 5.916)
        self.assertAlmostEqual(result["summary"][0]["ending_cash_usd"], 8.832)
        self.assertAlmostEqual(result["summary"][0]["net_pnl_usd"], 5.832)


if __name__ == "__main__":
    unittest.main()
