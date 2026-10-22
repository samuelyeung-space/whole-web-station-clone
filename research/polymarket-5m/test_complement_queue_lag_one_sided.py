"""Synthetic one-sided-book counterexamples; never financial evidence."""
import unittest
from unittest.mock import patch

import complement_queue_lag as lag


class OneSidedBookTests(unittest.TestCase):
    def replay_case(self, changes):
        def row(kind, t, seq, **kw):
            return dict(kind=kind, recv_ms=t, source_ts_ms=t,
                        connection_epoch=1, seq=seq, **kw)
        events = [row("clob_connection", 0, 0), row(
            "clob_snapshot", 1, 1, market_id="m1", asset_id="up",
            bids=[dict(price=.49, size=10)], asks=[dict(price=.51, size=10)])]
        events += [row("clob_price_change", t, i + 2, market_id="m1",
                       asset_id="up", **change)
                   for i, (t, change) in enumerate(changes)]
        mappings = [dict(market_id="m1", slot=0, up_token_id="up",
                         down_token_id="down", fee_rate=.07)]
        original = lag.DirectBook
        created = []
        def capture():
            value = original()
            created.append(value)
            return value
        with patch.object(lag, "DirectBook", capture):
            rows, counts = lag.replay(events, mappings, {"m1": "Up"},
                                      segment_end_ms=1000)
        return created[0], rows, counts

    def test_empty_bid_preserves_live_ask_without_trading(self):
        b, rows, counts = self.replay_case([(2, dict(
            side="BUY", price=.49, size=0, best_bid=0, best_ask=.51))])
        self.assertEqual(counts["venue_bbo_mismatch"], 0)
        self.assertEqual((b.bids, b.asks), ({}, {.51: 10}))
        self.assertFalse(b.snapshot_required)
        self.assertFalse(b.ready)
        self.assertIsNone(b.best_ask)
        self.assertEqual(rows, [])

    def test_empty_ask_preserves_live_bid_without_trading(self):
        b, rows, counts = self.replay_case([(2, dict(
            side="SELL", price=.51, size=0, best_bid=.49, best_ask=1))])
        self.assertEqual(counts["venue_bbo_mismatch"], 0)
        self.assertEqual((b.bids, b.asks), ({.49: 10}, {}))
        self.assertFalse(b.snapshot_required)
        self.assertFalse(b.ready)
        self.assertIsNone(b.best_bid)
        self.assertEqual(rows, [])

    def test_both_empty_is_valid_but_not_trade_ready(self):
        b, rows, counts = self.replay_case([
            (2, dict(side="BUY", price=.49, size=0, best_bid=0, best_ask=1)),
            (2, dict(side="SELL", price=.51, size=0, best_bid=0, best_ask=1))])
        self.assertEqual(counts["venue_bbo_mismatch"], 0)
        self.assertEqual((b.bids, b.asks), ({}, {}))
        self.assertFalse(b.snapshot_required)
        self.assertFalse(b.ready)

    def test_refill_uses_preserved_other_side(self):
        for empty_side, price, bid, ask in [("BUY", .49, 0, .51),
                                            ("SELL", .51, .49, 1)]:
            with self.subTest(empty_side=empty_side):
                b, _, counts = self.replay_case([
                    (2, dict(side=empty_side, price=price, size=0,
                             best_bid=bid, best_ask=ask)),
                    (3, dict(side=empty_side, price=price, size=8,
                             best_bid=.49, best_ask=.51))])
                self.assertEqual(counts["venue_bbo_mismatch"], 0)
                self.assertTrue(b.ready)
                self.assertEqual((b.best_bid, b.best_ask), (.49, .51))

    def test_incorrect_report_still_requires_new_snapshot(self):
        b, _, counts = self.replay_case([
            (2, dict(side="BUY", price=.49, size=0, best_bid=0, best_ask=.52)),
            (3, dict(side="BUY", price=.49, size=8))])
        self.assertEqual(counts["venue_bbo_mismatch"], 1)
        self.assertTrue(b.snapshot_required)
        self.assertEqual((b.bids, b.asks), ({}, {}))


if __name__ == "__main__":
    unittest.main()
