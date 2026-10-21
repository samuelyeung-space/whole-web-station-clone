import math
import unittest

import complement_queue_lag as lag


class VenueBboSentinelTests(unittest.TestCase):
    def test_empty_side_sentinels_are_valid_state(self):
        self.assertTrue(lag._valid_venue_bbo(0.99, 1.0))
        self.assertTrue(lag._valid_venue_bbo(0.0, 0.01))
        self.assertTrue(lag._valid_venue_bbo(0.0, 1.0))

    def test_wrong_boundaries_crosses_and_nonfinite_values_fail(self):
        for bid, ask in (
            (1.0, 1.0),
            (0.0, 0.0),
            (0.60, 0.50),
            (float("nan"), 0.50),
            (0.50, float("inf")),
        ):
            with self.subTest(bid=bid, ask=ask):
                self.assertFalse(lag._valid_venue_bbo(bid, ask))

    def test_explicit_empty_must_match_an_empty_local_side(self):
        self.assertTrue(lag._venue_level_matches(0.0, None, empty_sentinel=0.0))
        self.assertFalse(lag._venue_level_matches(0.0, 0.01, empty_sentinel=0.0))
        self.assertTrue(lag._venue_level_matches(1.0, None, empty_sentinel=1.0))
        self.assertFalse(lag._venue_level_matches(1.0, 0.99, empty_sentinel=1.0))

    def test_executable_level_requires_exact_local_bbo(self):
        self.assertTrue(lag._venue_level_matches(0.47, 0.47, empty_sentinel=0.0))
        self.assertFalse(lag._venue_level_matches(0.47, 0.46, empty_sentinel=0.0))
        self.assertTrue(lag._venue_level_matches(None, 0.46, empty_sentinel=0.0))


if __name__ == "__main__":
    unittest.main()
