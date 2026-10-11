"""Synthetic feature/ingestion checks; no backtest or profitability evidence."""
import unittest

from historical_flow_receipts import CoverageInterval, ReceiptFlowTape, normalize_hf_trade


def trade(tid="a", recv=59_000, source=58_999, price=100., size=1., side="buy", run="r"):
    return dict(exchange="binance", instrument="BTCUSDT", trade_id=tid,
                _run_id=run, recv_ts_ms=recv, trade_ts_ms=source,
                price=price, size=size, taker_side=side)


def coverage(start=0, end=60_000, known=60_000, status="complete", run="r"):
    return CoverageInterval("binance", "BTCUSDT", run, start, end, known,
                            status, "synthetic test coverage, not historical evidence")


class ReceiptFlowTests(unittest.TestCase):
    def tape(self):
        tape = ReceiptFlowTape()
        tape.add_coverage(coverage())
        return tape

    def test_quote_volume_not_base_volume(self):
        t = self.tape()
        t.add_trade(trade("a", price=100., size=1.))
        t.add_trade(trade("b", recv=59_500, price=200., size=1., side="sell"))
        f = t.features(60_000)
        self.assertAlmostEqual(f["features"]["flow_15s"], -1 / 3)
        self.assertAlmostEqual(f["features"]["flow_60s"], -1 / 3)
        self.assertEqual(f["feature_available_ms"]["flow_60s"], 60_000)

    def test_true_distinct_ids_with_identical_values_are_both_kept(self):
        t = self.tape()
        t.add_trade(trade("6580252386"))
        t.add_trade(trade("6580252387"))
        f = t.features(60_000)["audit"]["flow_15s"]
        self.assertEqual(f["unique_trade_count"], 2)
        self.assertEqual(f["quote_volume"], 200.)
        self.assertEqual(len(t.receipts), 2)

    def test_repeat_id_receipts_retained_but_economic_print_counted_once(self):
        t = self.tape()
        t.add_trade(trade("a"))
        t.add_trade(trade("a", recv=59_500))
        self.assertEqual(t.input_audit()["retained_receipts"], 2)
        self.assertEqual(t.input_audit()["unique_economic_trades"], 1)
        self.assertEqual(t.features(60_000)["audit"]["flow_15s"]["quote_volume"], 100.)

    def test_repeat_after_window_does_not_move_trade_first_receipt(self):
        t = self.tape()
        t.add_trade(trade("a", recv=10_000, source=9999))
        t.add_trade(trade("a", recv=59_000, source=9999))
        self.assertIsNone(t.features(60_000)["features"]["flow_15s"])
        self.assertEqual(t.features(60_000)["features"]["flow_60s"], 1.)

    def test_conflicting_repeat_id_never_rewrites_history(self):
        t = self.tape()
        t.add_trade(trade("a"))
        with self.assertRaisesRegex(ValueError, "conflicting repeated"):
            t.add_trade(trade("a", recv=59_500, price=101))
        self.assertEqual(t.first_receipts[0].price, 100.)

    def test_missing_and_unknown_sides_are_unknown_not_sell_or_zero(self):
        for side in (None, "mystery", "false", ""):
            t = self.tape()
            t.add_trade(trade("a", side=side))
            row = t.features(60_000)["audit"]["flow_15s"]
            self.assertIsNone(row["value"])
            self.assertEqual(row["status"], "unknown_aggressor_side")
            self.assertEqual(t.receipts[0].raw_taker_side, side)

    def test_explicit_balanced_flow_is_valid_zero(self):
        t = self.tape()
        t.add_trade(trade("a"))
        t.add_trade(trade("b", recv=59_500, side="sell"))
        self.assertEqual(t.features(60_000)["features"]["flow_15s"], 0.)

    def test_no_coverage_does_not_assume_dense_trades_prove_health(self):
        t = ReceiptFlowTape()
        for i in range(100):
            t.add_trade(trade(str(i), recv=59_000 + i))
        f = t.features(60_000)
        self.assertIsNone(f["features"]["flow_15s"])
        self.assertIsNone(f["feature_available_ms"]["flow_15s"])

    def test_explicit_gap_overrides_complete_claim(self):
        t = self.tape()
        t.add_trade(trade())
        t.add_coverage(coverage(58_000, 58_500, 58_501, "gap"))
        self.assertEqual(t.features(60_000)["audit"]["flow_15s"]["status"],
                         "unknown_explicit_gap_or_unhealthy")

    def test_uncovered_interval_cannot_be_joined_away(self):
        t = ReceiptFlowTape()
        t.add_trade(trade())
        t.add_coverage(coverage(0, 58_000, 58_000))
        t.add_coverage(coverage(58_001, 60_000, 60_000))
        self.assertEqual(t.features(60_000)["audit"]["flow_15s"]["status"],
                         "unknown_incomplete_coverage")

    def test_adjacent_same_epoch_intervals_cover_window(self):
        t = ReceiptFlowTape()
        t.add_trade(trade())
        t.add_coverage(coverage(0, 58_000, 58_000))
        t.add_coverage(coverage(58_000, 60_000, 60_000))
        self.assertEqual(t.features(60_000)["features"]["flow_60s"], 1.)

    def test_future_health_diagnostic_does_not_filter_past(self):
        t = self.tape()
        t.add_trade(trade())
        before = t.features(60_000)
        t.add_coverage(coverage(58_000, 59_000, 61_000, "unhealthy"))
        self.assertEqual(t.features(60_000), before)

    def test_future_complete_claim_cannot_enable_past(self):
        t = ReceiptFlowTape()
        t.add_trade(trade())
        t.add_coverage(coverage(0, 60_000, 60_001))
        self.assertIsNone(t.features(60_000)["features"]["flow_15s"])

    def test_future_received_print_and_source_reorder_do_not_leak(self):
        t = self.tape()
        t.add_trade(trade(source=58_000))
        before = t.features(60_000)
        # It happened earlier at the source but arrived after the decision.
        t.add_trade(trade("b", recv=60_001, source=57_000, side="sell", size=1000))
        self.assertEqual(t.features(60_000), before)

    def test_cross_epoch_window_is_unknown(self):
        t = self.tape()
        t.add_trade(trade())
        t.add_trade(trade("b", recv=59_500, run="r2"))
        t.add_coverage(coverage(run="r2"))
        self.assertEqual(t.features(60_000)["audit"]["flow_15s"]["status"], "unknown_cross_capture_epoch")

    def test_window_is_open_left_closed_right(self):
        t = self.tape()
        t.add_trade(trade("left", recv=45_000, side="sell", size=1000))
        t.add_trade(trade("right", recv=60_000, side="buy"))
        self.assertEqual(t.features(60_000)["features"]["flow_15s"], 1.)

    def test_exact_ids_raw_clocks_and_raw_side_are_preserved(self):
        row = trade("9007199254740993123", source=58_123.25, side=" SeLL ")
        normalized = normalize_hf_trade(row)
        self.assertEqual(normalized.trade_id, row["trade_id"])
        self.assertEqual(normalized.source_event_ms, row["trade_ts_ms"])
        self.assertEqual(normalized.local_recv_ms, row["recv_ts_ms"])
        self.assertEqual(normalized.raw_taker_side, row["taker_side"])
        self.assertEqual(normalized.aggressor_side, "SELL")
        with self.assertRaises(ValueError):
            normalize_hf_trade(trade(9007199254740992.))

    def test_receipt_backwards_and_future_interval_attestation_rejected(self):
        t = self.tape()
        t.add_trade(trade())
        with self.assertRaisesRegex(ValueError, "receipt clock moved backwards"):
            t.add_trade(trade("b", recv=58_000))
        with self.assertRaisesRegex(ValueError, "not-yet-observed"):
            coverage(0, 60_000, 59_000)

    def test_existing_template_can_consume_features_without_new_rules(self):
        from mine500_catalog import make_candidates, signal
        t = self.tape()
        t.add_trade(trade())
        candidate = next(c for c in make_candidates() if c["id"] == "M500-181")
        f = dict(t.features(60_000)["features"], tau_s=20.)
        self.assertEqual(signal(candidate, f), "Up")


if __name__ == "__main__":
    unittest.main()
