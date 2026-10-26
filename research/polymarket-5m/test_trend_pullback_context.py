import math
import unittest
from trend_pullback_context import TrendContext


def row(t, price=100., epoch=1, kind="spot_trade"):
    return dict(recv_ms=t, source_ts_ms=t+10000000, connection_epoch=epoch,
                stream_sequence=int(t), kind=kind, price=price, size=1)


def warmed():
    c = TrendContext()
    c.ingest(row(0, kind="spot_connection"))
    # Bucket zero is discarded; eleven complete buckets follow. The high and
    # low of every accepted bar are close +/-1, so every TR equals exactly2.
    for second in range(720):
        bar, within = divmod(second, 60)
        close = 100. + bar
        price = close + 1 if within == 0 else close - 1 if within == 1 else close
        c.ingest(row(second*1000, price))
    c.ingest(row(720000, 112.))
    return c


class ContextTests(unittest.TestCase):
    def test_known_ma_atr_closed_bars(self):
        c = warmed(); f = c.snapshot(720000)
        self.assertAlmostEqual(c.sma, 106.5)  # closes102..111
        self.assertAlmostEqual(c.sma_prev, 105.5)  # closes101..110
        self.assertAlmostEqual(c.atr, 2.)
        self.assertEqual(f["ctx_ma_direction"], 1)
        self.assertIn(f["ctx_st_direction"], (-1, 1))
        self.assertEqual(c.counts["closed_bars"], 11)
        self.assertEqual(c.counts["first_bucket_discarded"], 1)

    def test_future_append_does_not_change_snapshot(self):
        c = warmed(); old = c.snapshot(720000)
        c.ingest(row(721000, 1.))
        self.assertEqual(c.snapshot(720000), old)
        self.assertEqual(c.snapshot(721000)["ctx_price"], 1.)

    def test_known_supertrend_initial_band_and_cross(self):
        c = warmed(); f = c.snapshot(720000)
        self.assertEqual(c.upper, 116.)
        self.assertEqual(c.lower, 105.)
        self.assertEqual(c.supertrend, 116.)
        self.assertEqual(f["ctx_st_direction"], -1)
        for second in range(720, 1080):
            bar, within = divmod(second, 60)
            close = 100. + bar
            price = close+1 if within == 0 else close-1 if within == 1 else close
            c.ingest(row(second*1000, price))
        c.ingest(row(1080000, 118.))
        f = c.snapshot(1080000)
        self.assertEqual(c.atr, 2.)
        self.assertEqual(c.supertrend, 111.)
        self.assertEqual(f["ctx_st_direction"], 1)

    def test_partial_current_bar_not_a_close(self):
        c = warmed(); c.snapshot(720000); sma, atr = c.sma, c.atr
        c.ingest(row(720500, 9999.)); c.snapshot(720500)
        self.assertEqual(c.sma, sma); self.assertEqual(c.atr, atr)

    def test_warmup_unavailable_and_first_partial_discarded(self):
        c = TrendContext(); c.ingest(row(30123, kind="spot_connection"))
        for t in range(31000, 600000, 1000): c.ingest(row(t))
        f = c.snapshot(599000)
        self.assertTrue(all(v is None for v in f.values()))
        self.assertEqual(c.counts["first_bucket_discarded"], 1)

    def test_gap_resets_without_forward_fill(self):
        c = warmed(); c.snapshot(720000)
        c.ingest(row(726000, 112.)); f = c.snapshot(726000)
        self.assertTrue(all(v is None for v in f.values()))
        self.assertEqual(c.counts["reset_trade_gap"], 1)
        self.assertIsNone(c.sma)

    def test_epoch_reset(self):
        c = warmed(); c.snapshot(720000)
        c.ingest(row(720500, 112., epoch=2)); f = c.snapshot(720500)
        self.assertTrue(all(v is None for v in f.values()))
        self.assertEqual(c.counts["reset_epoch_transition"], 2)

    def test_disconnect_resets(self):
        c = warmed(); c.snapshot(720000)
        c.ingest(row(720500, kind="spot_disconnect"))
        self.assertTrue(all(v is None for v in c.snapshot(720500).values()))
        self.assertFalse(c.connected)

    def test_receipt_regression_resets(self):
        c = warmed(); c.snapshot(720000)
        c.ingest(row(719900, 112.)); f = c.snapshot(720000)
        self.assertTrue(all(v is None for v in f.values()))
        self.assertEqual(c.counts["reset_receipt_regression"], 1)

    def test_freshness_and_known_r1(self):
        c = warmed(); f = c.snapshot(720000)
        self.assertAlmostEqual(f["ctx_r1_bps"], 10000*math.log(112/111))
        self.assertTrue(all(v is None for v in c.snapshot(721001).values()))

    def test_backward_decision_fails_closed(self):
        c = warmed(); c.snapshot(720000)
        with self.assertRaises(ValueError): c.snapshot(719999)

    def test_source_clock_is_not_bar_clock(self):
        c = warmed(); c.snapshot(720000)
        self.assertEqual(c.bar["start"], 720000)
        self.assertFalse(c.audit()["aggressor_side_inferred"])
        self.assertFalse(c.audit()["market_or_feed_completeness_claim"])


if __name__ == "__main__": unittest.main(verbosity=2)
