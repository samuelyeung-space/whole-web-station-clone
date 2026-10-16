"""Engineering-only tests for report aggregation; not trading evidence."""
import unittest

from mar_factorial_diagnostics import paired_gates, gate_summary, stats


def row(cid, slope, smooth, pnl):
    return dict(candidate_id=cid, mechanism="trend_pullback", window=15,
        reference_slope_gate=slope, smooth_only=smooth, band=0.5, edge=0.04,
        scenario="shares5_base_release0", latency_ms=500, recorded_scope_net_pnl_usd=pnl,
        execution_complete_on_recorded_scope=pnl is not None,
        engine_status="simulated_complete" if pnl is not None else "evaluated_no_signal",
        positive_on_both_prediction_dates=False, attempts=int(pnl is not None), filled_orders=int(pnl is not None),
        filled_shares=5 if pnl is not None else 0, fees_usd=0.1 if pnl is not None else 0,
        recorded_scope_pnl_per_filled_share_usd=pnl / 5 if pnl is not None else None,
        max_realized_drawdown_usd=0, min_available_cash_usd=995 if pnl is not None else 1000,
        ending_cash_usd=1000 + pnl if pnl is not None else 1000)


class FactorialReportTest(unittest.TestCase):
    def test_null_not_imputed_or_in_median(self):
        result = stats([row("A", False, False, -2), row("B", False, True, None)])
        self.assertEqual(result["recorded_scope_net_pnl_usd_median"], -2)
        self.assertEqual(result["null_pnl_variants"], 1)
        self.assertEqual(result["evaluated_no_signal_variants"], 1)
        self.assertEqual(result["zero_variants"], 0)

    def test_matched_gate_null_and_cash_separate(self):
        values = [row("A", False, False, -2), row("B", False, True, None),
                  row("C", True, False, -1), row("D", True, True, None)]
        pairs = paired_gates(values)
        self.assertEqual(len(pairs), 4)
        smooth = [r for r in pairs if r["gate"] == "smooth_only"]
        self.assertTrue(all(r["gate_on_minus_off_pnl_usd"] is None for r in smooth))
        self.assertEqual(sorted(r["gate_on_minus_off_ending_cash_usd"] for r in smooth), [1, 2])
        summary = [r for r in gate_summary(pairs) if r["gate"] == "smooth_only"][0]
        self.assertEqual(summary["both_pnl_nonnull_pairs"], 0)
        self.assertEqual(summary["at_least_one_null_pnl_pairs"], 2)
        self.assertIsNone(summary["gate_on_minus_off_pnl_median_usd"])

    def test_duplicate_pair_rejected(self):
        value = row("A", False, False, 1)
        with self.assertRaisesRegex(ValueError, "duplicate paired factor key"):
            paired_gates([value, value])

    def test_missing_pair_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing matched gate pair"):
            paired_gates([row("A", False, False, 1)])


if __name__ == "__main__":
    unittest.main()
