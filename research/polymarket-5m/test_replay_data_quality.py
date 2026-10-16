"""Deterministic synthetic checks for input quality without market network access."""
import unittest
import replay_data_quality as q

T0 = 1_800_000_000_000  # aligned five-minute UTC slot

def rows(books=None, features=None, outcome=True, healthy=True, side_specific=False):
    # align arbitrary timestamp to the UTC five-minute grid
    start = (T0 // q.SLOT) * q.SLOT
    base = [
        {"type": "dataset", "recv_ms": start, "dataset_id": "synthetic"},
        {"type": "market", "recv_ms": start, "market_id": "m", "start_ms": start},
    ]
    if books is not None:
        book = {"type": "book", "recv_ms": start + 100, "market_id": "m",
                "up_asks": [[.51, 100]], "down_asks": [[.50, 100]],
                "healthy": healthy}
        if side_specific:
            book.update(healthy=False, execution_mode="up_only_limited",
                        up_healthy=True, down_healthy=False,
                        up_recv_ms=start + 50, down_recv_ms=None)
        base.append(book)
    if features is not None:
        base.append({"type": "decision", "recv_ms": start + 200,
                     "market_id": "m", "features": features,
                     "feature_available_ms": {"r_5s": start + 190}})
    if outcome:
        base.append({"type": "outcome", "recv_ms": start + 300,
                     "market_id": "m", "winner": "Up"})
    return base, start

def inspect(events, start, required=("r_5s",), max_age=250):
    return q.summarize(events, [[start, start + q.SLOT]], required, max_age)["slots"][0]

class DataQualityTests(unittest.TestCase):
    def test_complete_native_inputs(self):
        events, start = rows(books=True, features={"r_5s": 1.0})
        self.assertEqual(inspect(events, start)["primary_blocker"],
                         "input_checks_passed_not_execution_verified")
    def test_missing_down_never_manufactured(self):
        events, start = rows(books=True, features={"r_5s": 1.0}, side_specific=True)
        self.assertEqual(inspect(events, start)["primary_blocker"], "no_fresh_down_book")
    def test_no_market_is_not_no_recording(self):
        start = (T0 // q.SLOT) * q.SLOT
        result = inspect([], start)
        self.assertEqual(result["primary_blocker"], "no_market_registration")
    def test_partial_span_is_not_full(self):
        events, start = rows(books=True, features={"r_5s": 1.0})
        result = q.summarize(events, [[start + 1, start + q.SLOT]])["slots"][0]
        self.assertEqual(result["primary_blocker"], "partial_declared_recording_span")
    def test_missing_feature_kept_explicit(self):
        events, start = rows(books=True, features={"r_5s": None})
        self.assertEqual(inspect(events, start)["primary_blocker"], "required_features_unavailable")
    def test_future_feature_cannot_pass(self):
        events, start = rows(books=True, features={"r_5s": 1.0})
        events[-2]["feature_available_ms"]["r_5s"] = start + 250
        self.assertEqual(inspect(events, start)["valid_feature_decisions"], 0)
    def test_outcome_missing_is_not_zero(self):
        events, start = rows(books=True, features={"r_5s": 1.0}, outcome=False)
        self.assertEqual(inspect(events, start)["primary_blocker"], "no_official_outcome")
    def test_absent_span_is_separate(self):
        start = (T0 // q.SLOT) * q.SLOT
        result = q.summarize([], [[start + q.SLOT, start + 2*q.SLOT]])
        self.assertEqual(result["slots"][0]["primary_blocker"], "no_declared_recording_span")
    def test_no_silent_invalid_intervals(self):
        with self.assertRaises(ValueError):
            q.summarize([], [[0, 0]])
        with self.assertRaises(ValueError):
            q.summarize([], [[0, 1000]], max_quote_age_ms=-1)
    def test_native_book_stale(self):
        events, start = rows(books=True, features={"r_5s": 1.0})
        events[2]["up_recv_ms"] = start - 300
        self.assertEqual(inspect(events, start)["primary_blocker"], "no_fresh_up_book")
    def test_input_does_not_generate_pnl(self):
        events, start = rows(books=True, features={"r_5s": 1.0})
        result = q.summarize(events, [[start, start + q.SLOT]], ("r_5s",))
        self.assertIsNone(result["pnl"])
        self.assertIsNone(result["formal_oos_result"])
        self.assertEqual(result["days"][0]["expected_slots"], 288)

if __name__ == "__main__":
    unittest.main()
