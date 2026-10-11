"""Synthetic execution counterexamples, never evidence of strategy returns."""
import unittest

from historical100_execution import replay


def book(t, ask=.5, ask_qty=1000., bid=.49, bid_qty=1000., changed=None):
    return dict(recv_ms=t, active=True, halted=False,
                changed_ms=t if changed is None else changed,
                up=dict(asks=[[ask, ask_qty]], bids=[[bid, bid_qty]]),
                down=dict(asks=[[.51, 1000.]], bids=[[.50, 1000.]]))


def market(end=100_000., outcome="up", known=400_000.):
    return dict(end_ms=end, outcome=outcome, outcome_known_ms=known,
                fee_rate=.07, fee_source="test-only explicit coefficient")


def signal(sid="a", t=1000., side="up", rule="R1", mid="m"):
    return dict(signal_id=sid, market_id=mid, decision_ms=t, side=side, rule_id=rule)


class LedgerTests(unittest.TestCase):
    def run_one(self, tape, sigs=None, m=None, rule="R1", **kw):
        return replay({"m": m or market()}, {"m": tape}, sigs or [signal(rule=rule)],
                      rule_id=rule, latency_ms=500., **kw)

    def test_partial_fixed_limit_not_future_exit_sizing(self):
        out = self.run_one([book(1000), book(1500, ask_qty=40)])
        a = out["attempts"][0]
        self.assertEqual(a["requested_shares"], 100)
        self.assertEqual(a["filled_shares"], 40)
        self.assertAlmostEqual(a["cost_usd"], 20.7)
        self.assertAlmostEqual(out["summary"]["net_pnl_usd"], 19.3)

    def test_full100_policy_rejects_partial_without_consuming_depth(self):
        tape = [book(1000, ask_qty=120), book(1500, ask_qty=120)]
        sigs = [signal("a"), signal("b")]
        out = self.run_one(tape, sigs, entry_fill_policy="full100_or_zero")
        self.assertEqual([a["filled_shares"] for a in out["attempts"]], [100, 0])
        self.assertEqual(out["attempts"][1]["status"],
                         "rejected_full100_depth_or_limit")
        self.assertEqual(out["summary"]["partial_fills"], 0)

    def test_failed_full100_order_does_not_consume_displayed_depth(self):
        tape = [book(1000, ask_qty=80), book(1500, ask_qty=80),
                book(1600, ask_qty=100)]
        sigs = [signal("a"), signal("b", t=1100)]
        out = self.run_one(tape, sigs, entry_fill_policy="full100_or_zero")
        self.assertEqual([a["filled_shares"] for a in out["attempts"]], [0, 100])
        self.assertEqual(out["attempts"][0]["status"],
                         "rejected_full100_depth_or_limit")

    def test_invalid_entry_fill_policy_rejected(self):
        with self.assertRaisesRegex(ValueError, "entry fill policy"):
            self.run_one([book(1000), book(1500)],
                         entry_fill_policy="request100_but_partial")

    def test_ask_rise_after_decision_is_failed_fixed_order(self):
        out = self.run_one([book(1000), book(1500, ask=.51)])
        a = out["attempts"][0]
        self.assertEqual(a["limit_price"], .5)
        self.assertEqual(a["filled_shares"], 0)
        self.assertEqual(out["summary"]["ending_cash_usd"], 1000)

    def test_continuous_duplicate_snapshots_do_not_replenish(self):
        tape = [book(1000, ask_qty=120), book(1100, ask_qty=120),
                book(1200, ask_qty=120), book(1500, ask_qty=120),
                book(1600, ask_qty=120), book(1700, ask_qty=121)]
        sigs = [signal("a"), signal("b"), signal("c", 1100), signal("d", 1200)]
        out = self.run_one(tape, sigs)
        self.assertEqual([a["filled_shares"] for a in out["attempts"]], [100, 20, 0, 100])
        self.assertEqual(len(out["attempts"]), 4)

    def test_reserved_cash_rejects_without_loan_or_resize(self):
        sigs = [signal(str(i)) for i in range(25)]
        out = self.run_one([book(1000, ask_qty=3000), book(1500, ask_qty=3000)], sigs)
        s = out["summary"]
        self.assertEqual(s["cash_rejections"], 6)
        self.assertEqual(s["full_fills"], 19)
        self.assertEqual(s["partial_fills"], 0)
        self.assertGreaterEqual(s["min_available_cash_usd"], 0)

    def test_later_outcome_not_available_to_earlier_orders(self):
        ms = {"m": market(), "n": market(end=200_000., known=500_000.)}
        sigs = [signal(str(i)) for i in range(19)] + [signal("n", 101_000., mid="n")]
        tapes = {"m": [book(1000, ask_qty=3000), book(1500, ask_qty=3000)],
                 "n": [book(101_000.), book(101_500.)]}
        out = replay(ms, tapes, sigs, rule_id="R1", latency_ms=500)
        self.assertEqual(out["attempts"][-1]["status"], "rejected_insufficient_cash")

    def test_partial_exit_carries_all_remaining_inventory_to_settlement(self):
        tape = [book(1000), book(1500), book(61_500., ask=.62, bid=.6, bid_qty=30),
                book(62_000., ask=.63, bid=.61, bid_qty=30)]
        out = self.run_one(tape, rule="R2", m=market(outcome="down"))
        a, x = out["attempts"][0], out["exits"][0]
        self.assertEqual(a["filled_shares"], 100)
        self.assertEqual(x["decision_ms"], 61_500.)
        self.assertEqual(x["limit_price"], .6)
        self.assertEqual(x["filled_shares"], 30)
        self.assertEqual(a["settlement_shares"], 70)
        self.assertAlmostEqual(out["summary"]["net_pnl_usd"], 30 * .61 - 30 * .07 * .61 * .39 - 51.75)

    def test_exit_bid_falls_after_send_no_retry_with_hindsight(self):
        tape = [book(1000), book(1500), book(31_500., ask=.62, bid=.6),
                book(32_000., ask=.61, bid=.59), book(33_000., ask=.63, bid=.62)]
        out = self.run_one(tape, rule="R3")
        self.assertEqual(len(out["exits"]), 1)
        self.assertEqual(out["exits"][0]["filled_shares"], 0)
        self.assertEqual(out["attempts"][0]["settlement_shares"], 100)

    def test_partial_entry_below_exit_order_minimum_waits_for_settlement(self):
        tape = [book(1000), book(1500, ask_qty=2),
                book(31_500., ask=.62, bid=.6), book(32_000., ask=.63, bid=.61)]
        out = self.run_one(tape, rule="R3")
        self.assertEqual(out["exits"][0]["status"], "not_submitted_below_minimum_residual_to_settle")
        self.assertEqual(out["attempts"][0]["settlement_shares"], 2)

    def test_unknown_outcome_does_not_disappear_or_become_zero_profit(self):
        out = self.run_one([book(1000), book(1500)], m=market(outcome=None, known=None))
        self.assertIsNone(out["summary"]["net_pnl_usd"])
        self.assertEqual(out["summary"]["unresolved_positions"], 1)
        self.assertAlmostEqual(out["summary"]["remaining_cost_usd"], 51.75)

    def test_imputed_outcome_time_is_explicit_five_minute_default(self):
        out = self.run_one([book(1000), book(1500)], m=market(known=None))
        self.assertTrue(out["attempts"][0]["outcome_time_imputed"])
        self.assertEqual(out["attempts"][0]["settlement_ms"], 400_000.)
        self.assertFalse(out["summary"]["actual_cash_reuse_validated"])

    def test_future_book_is_not_used_for_decision(self):
        out = self.run_one([book(1001), book(1500)])
        self.assertEqual(out["attempts"][0]["status"], "rejected_decision_missing_book")

    def test_frozen_signal_limit_must_equal_raw_causal_quote(self):
        sig = signal()
        sig.update(asklimit=.49, signal_status="ready")
        out = self.run_one([book(1000), book(1500)], [sig])
        self.assertEqual(out["attempts"][0]["status"], "rejected_frozen_decision_limit_mismatch")
        self.assertEqual(out["summary"]["filled_orders"], 0)

    def test_invalid_signal_remains_recorded_without_order(self):
        sig = signal()
        sig.update(asklimit=None, signal_status="invalid_decision_limit")
        out = self.run_one([book(1000), book(1500)], [sig])
        self.assertEqual(len(out["attempts"]), 1)
        self.assertEqual(out["attempts"][0]["status"], "rejected_invalid_frozen_decision_limit")

    def test_books_can_be_lazy_mapping_without_iteration(self):
        class LazyBooks:
            def __getitem__(self, key):
                if key != "m":
                    raise KeyError(key)
                return [book(1000), book(1500)]
            def __iter__(self):
                raise AssertionError("must not flatten all daily tapes")
        out = replay({"m": market()}, LazyBooks(), [signal()], rule_id="R1", latency_ms=500)
        self.assertEqual(out["summary"]["filled_orders"], 1)


if __name__ == "__main__":
    unittest.main()
