"""Synthetic causality/invalid-book regression tests, never PnL evidence."""
import unittest

import complement_queue_lag as lag


def book():
    b = lag.DirectBook()
    b.replace([{"price": .49, "size": 10}],
              [{"price": .51, "size": 10}], 1000, 1000)
    return b


def fixture():
    def row(kind, t, seq, **kw):
        return dict(kind=kind, recv_ms=t, source_ts_ms=t,
                    connection_epoch=1, seq=seq, **kw)
    def snap(asset, seq, bids, asks):
        return row("clob_snapshot", 1, seq, market_id="m1", asset_id=asset,
                   bids=[dict(price=p, size=q) for p, q in bids],
                   asks=[dict(price=p, size=q) for p, q in asks])
    def delta(asset, t, seq, side, price, size):
        return row("clob_price_change", t, seq, market_id="m1", asset_id=asset,
                   side=side, price=price, size=size)
    rows = [row("clob_connection", 0, 0),
            snap("up", 1, [(.49, 5), (.47, 10)], [(.51, 10)]),
            snap("down", 2, [(.46, 10)], [(.48, 10)]),
            delta("up", 100000, 3, "BUY", .49, 0),
            delta("up", 100000, 4, "SELL", .51, 0),
            delta("up", 100000, 5, "SELL", .49, 10),
            delta("down", 100100, 6, "SELL", .48, 10)]
    mappings = [dict(market_id="m1", slot=0, up_token_id="up",
                     down_token_id="down", fee_rate=.07)]
    return rows, mappings, delta


class FailClosedTests(unittest.TestCase):
    def test_invalid_delta_cannot_resurrect_without_snapshot(self):
        for side, price, size in [("SELL", .51, float("nan")),
                                  ("BUY", float("inf"), 1),
                                  ("SELL", .51, -1), ("INVALID", .51, 1),
                                  (None, .51, 1), ("BUY", "bad", 1)]:
            with self.subTest(side=side, price=price, size=size):
                b = book()
                with self.assertRaises(ValueError):
                    b.change(side, price, size, 1100, 1100)
                b.change("BUY", .49, 9, 1200, 1200)
                self.assertFalse(b.valid_at(1200, 2000))
                self.assertTrue(b.snapshot_required)
                self.assertEqual((b.bids, b.asks), ({}, {}))
                b.replace([dict(price=.49, size=8)],
                          [dict(price=.51, size=8)], 1300, 1300)
                self.assertTrue(b.valid_at(1300, 2000))

    def test_snapshot_validation_is_atomic(self):
        b = book()
        with self.assertRaises(ValueError):
            b.replace([dict(price=.48, size=8)],
                      [dict(price=.52, size=float("nan"))], 1100, 1100)
        self.assertEqual((b.bids, b.asks), ({}, {}))
        self.assertTrue(b.snapshot_required)

    def test_nonfinite_clocks_fail_closed(self):
        for recv, source in [(float("nan"), 1100), (1100, float("inf"))]:
            b = book()
            with self.assertRaises(ValueError):
                b.change("BUY", .49, 9, recv, source)
            self.assertFalse(b.ready)
            self.assertTrue(b.snapshot_required)

    def test_valid_signal_and_matching_times_unchanged(self):
        events, mappings, _ = fixture()
        rows, counts = lag.replay(events, mappings, {"m1": "Down"},
                                  segment_end_ms=101000)
        base = [r for r in rows if r["variant"] == "base"]
        self.assertEqual([r["evaluation_ms"] for r in base], [400, 500])
        self.assertTrue(all(r["filled_shares"] == 5 for r in base))
        self.assertEqual(counts["base_signals"], 1)

    def test_invalid_delta_between_decision_and_match_aborts_replay(self):
        events, mappings, delta = fixture()
        events += [delta("down", 100300, 7, "SELL", .48, float("nan")),
                   delta("down", 100400, 8, "BUY", .46, 9)]
        with self.assertRaisesRegex(ValueError, "invalid direct-book delta"):
            lag.replay(events, mappings, {"m1": "Down"}, segment_end_ms=101000)

    def test_invalid_venue_bbo_fails_closed_without_aborting_segment(self):
        cases = [
            ("bad", .48, "malformed"),
            (.48, .48, "locked"),
            (.49, .48, "crossed"),
            (float("nan"), .48, "nonfinite"),
        ]
        for best_bid, best_ask, label in cases:
            with self.subTest(label=label):
                events, mappings, delta = fixture()
                invalid = delta("down", 100300, 7, "SELL", .48, 9)
                invalid.update(best_bid=best_bid, best_ask=best_ask)
                # An ordinary delta after invalidation cannot resurrect the
                # book.  Only a new full snapshot may do that.
                events += [invalid, delta("down", 100350, 8, "BUY", .46, 9)]
                rows, counts = lag.replay(
                    events, mappings, {"m1": "Down"},
                    segment_end_ms=101000,
                )
                base = [r for r in rows if r["variant"] == "base"]
                self.assertEqual(counts["invalid_venue_bbo"], 1)
                self.assertEqual(
                    [r["reason"] for r in base],
                    ["book_unavailable", "book_unavailable"],
                )
                self.assertTrue(all(r["filled_shares"] == 0 for r in base))


if __name__ == "__main__":
    unittest.main()
