"""Synthetic one-token recordings: execution coverage, never profit evidence."""
import copy
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import replay_engine as ex
import replay_runner as rr


def signal(candidate, features):
    return candidate["side"]


class SideSpecificBookTests(unittest.TestCase):
    @staticmethod
    def config(**kwargs):
        return ex.ReplayConfig(**{
            "side_specific_books": True, "latencies_ms": (500,),
            "max_quote_age_ms": 1000, **kwargs})

    @staticmethod
    def book(now=100):
        return {"type": "book", "recv_ms": now, "market_id": "m",
                "up_recv_ms": now, "up_asks": [[0.4, 100]],
                "side_healthy": {"Up": True, "Down": False},
                "healthy": False, "fee_rate": 0.07}

    def events(self, match_book=None):
        events = [
            {"type": "dataset", "recv_ms": 0, "dataset_id": "synthetic-side",
             "split": "development", "coverage_end_ms": 2000,
             "provenance": {"source": "synthetic-test-not-backtest"}},
            {"type": "market", "recv_ms": 0, "market_id": "m",
             "start_ms": 0, "end_ms": 300000, "label_source": "gamma"},
            self.book(),
            {"type": "decision", "recv_ms": 100, "market_id": "m",
             "features": {"tau_s": 299.9},
             "feature_available_ms": {"tau_s": 100}},
        ]
        if match_book is not None:
            events.append(match_book)
        events.append({"type": "outcome", "recv_ms": 300000, "market_id": "m",
                       "resolved_ms": 300000, "source": "gamma", "winner": "Up"})
        return events

    def run_side(self, side, events=None, config=None):
        return ex.replay_events(events or self.events(),
            [{"id": side, "side": side, "required_features": []}], signal,
            config or self.config())

    def test_explicit_opt_in_accepts_up_and_never_synthesizes_down(self):
        source = self.events()
        before = copy.deepcopy(source)
        up = self.run_side("Up", source)
        self.assertEqual(source, before)
        self.assertEqual(up["attempts"][0]["status"], "filled_full")
        self.assertAlmostEqual(up["summary"][0]["net_pnl_usd"], 2.916)
        self.assertTrue(up["config"]["side_specific_books"])
        down = self.run_side("Down", source)
        self.assertEqual(down["attempts"][0]["status"], "rejected_missing_down_book_at_decision")
        self.assertEqual(down["summary"][0]["status"], "incomplete_missing_execution_inputs")
        self.assertTrue(down["summary"][0]["missing_execution_inputs"])
        self.assertIsNone(down["summary"][0]["net_pnl_usd"])

    def test_default_continues_to_require_both_sides(self):
        events = self.events()
        events[2]["healthy"] = True
        result = self.run_side("Up", events, self.config(side_specific_books=False))
        self.assertEqual(result["attempts"][0]["status"], "rejected_missing_asks_at_decision")
        self.assertIsNone(result["summary"][0]["net_pnl_usd"])
        with self.assertRaisesRegex(ValueError, "side_specific_books must be boolean"):
            self.config(side_specific_books=1)

    def test_other_side_staleness_health_and_invalid_depth_do_not_affect_up(self):
        book = self.book()
        book.update(down_recv_ms=-100000, down_asks="invalid")
        valid, error = ex._book(book, 100, self.config(), "Up")
        self.assertIsNone(error)
        self.assertNotIn("Down", valid)
        book["up_recv_ms"] = -100000
        self.assertEqual(ex._book(book, 100, self.config(), "Up")[1], "stale_up_book")

    def test_selected_receipt_and_health_must_be_explicit(self):
        for removed, expected in (("up_recv_ms", "missing_up_book_timestamp"),
                                  ("side_healthy", "missing_up_book_health")):
            events = self.events()
            events[2].pop(removed)
            events[2]["healthy"] = True
            result = self.run_side("Up", events)
            self.assertEqual(result["attempts"][0]["status"], "rejected_" + expected + "_at_decision")
            self.assertIsNone(result["summary"][0]["net_pnl_usd"])
        book = self.book()
        book.pop("side_healthy")
        book["up_healthy"] = True
        self.assertIsNone(ex._book(book, 100, self.config(), "Up")[1])

    def test_selected_receipt_cannot_be_future_relative_to_enclosing_event(self):
        book = self.book()
        book["up_recv_ms"] = 200
        self.assertEqual(ex._book(book, 600, self.config(), "Up")[1], "stale_up_book")
        book = self.book(700)
        self.assertEqual(ex._book(book, 600, self.config(), "Up")[1], "stale_up_book")

    def test_missing_selected_match_book_keeps_reservation_released_and_pnl_null(self):
        match = self.book(600)
        match.pop("up_asks")
        match["down_asks"] = [[0.6, 100]]
        result = self.run_side("Up", self.events(match))
        self.assertEqual(result["attempts"][0]["status"], "rejected_missing_up_book_at_match")
        self.assertEqual(result["summary"][0]["ending_reserved_usd"], 0)
        self.assertEqual(result["summary"][0]["ending_cash_usd"], 1000)
        self.assertIsNone(result["summary"][0]["net_pnl_usd"])

    def test_side_lane_still_checks_fees_and_selected_depth(self):
        for field, value, expected in (
                ("fee_rate", None, "missing_or_invalid_fee"),
                ("up_asks", [[1.5, 10]], "invalid_asks_up"),
                ("side_healthy", {"Up": False}, "unhealthy_up_book")):
            book = self.book()
            book[field] = value
            self.assertEqual(ex._book(book, 100, self.config(), "Up")[1], expected)

    def test_projection_permits_only_explicitly_declared_execution_sides(self):
        events = self.events()
        events[0].update(book_resolution="decision_and_match_projection",
                         supported_latencies_ms=[500], decision_grid_ms=100,
                         execution_book_sides=["Up"])
        self.assertEqual(self.run_side("Up", events)["attempts"][0]["status"], "filled_full")
        # Even an accidental opposite-side column cannot make an undeclared
        # token executable. No complement is constructed from the Up book.
        events[2].update(down_asks=[[0.6, 100]], down_recv_ms=100)
        events[2]["side_healthy"]["Down"] = True
        down = self.run_side("Down", events)
        self.assertEqual(down["attempts"][0]["status"], "rejected_missing_down_book_at_decision")
        self.assertIsNone(down["summary"][0]["net_pnl_usd"])

    def test_projection_side_declaration_and_receipts_are_mandatory(self):
        for sides in (None, [], ["Unknown"], ["Up", "Up"]):
            events = self.events()
            events[0].update(book_resolution="decision_and_match_projection",
                             supported_latencies_ms=[500], decision_grid_ms=100,
                             execution_book_sides=sides)
            with self.assertRaisesRegex(ValueError, "execution_book_sides"):
                self.run_side("Up", events)
        events[0]["execution_book_sides"] = ["Up"]
        events[2].pop("up_recv_ms")
        with self.assertRaisesRegex(ValueError, "declared original side"):
            self.run_side("Up", events)

    def test_default_projection_still_rejects_missing_other_side_receipt(self):
        events = self.events()
        events[0].update(book_resolution="decision_and_match_projection",
                         supported_latencies_ms=[500], decision_grid_ms=100,
                         execution_book_sides=["Up"])
        with self.assertRaisesRegex(ValueError, "both original side"):
            self.run_side("Up", events, self.config(side_specific_books=False))

    def test_shared_dispatch_uses_the_same_side_contract(self):
        name = "side_plugin_" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, name + ".py").write_text(
                "def catalog():\n"
                "    return [{'id':'U','side':'Up','required_features':[]},"
                "{'id':'D','side':'Down','required_features':[]}]\n"
                "def signal(c,f):\n    return c['side']\n")
            sys.path.insert(0, directory)
            try:
                importlib.invalidate_caches()
                up = rr.execute(self.events(), [name + ":*"], self.config(), mode="shared", priority=["U", "D"])
                self.assertEqual(up["attempts"][0]["status"], "filled_full")
                down = rr.execute(self.events(), [name + ":*"], self.config(), mode="shared", priority=["D", "U"])
                self.assertEqual(down["attempts"][0]["status"], "rejected_missing_down_book_at_decision")
                self.assertIsNone(down["summary"][0]["net_pnl_usd"])
            finally:
                sys.path.remove(directory)
                sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
