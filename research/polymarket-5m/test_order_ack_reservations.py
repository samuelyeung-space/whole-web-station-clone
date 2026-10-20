"""Synthetic accounting regressions only: no historical samples or alpha tests."""
import dataclasses
import math
import random
import unittest

from paired_leg_inventory import LegIntent, ReplayConfig, replay


def order(oid="a", *, send=0, arrival=100, ack=400, ask=.4, depth=100,
          side="YES", settlement=5000, release=5000):
    return LegIntent(oid, oid, "m", side, send, arrival, settlement, release,
                     True, 100, .6, ask, depth, send, None, ack)


def config(**kwargs):
    return ReplayConfig(initial_cash=110, reserve_cash_at_send=True,
                        reserve_until_ack=True, **kwargs)


class AckReservationTests(unittest.TestCase):
    def test_full_fill_residual_not_reused_before_ack(self):
        a, b = order(), order("b", send=200, arrival=300, ack=600)
        stressed = replay([a, b], config())
        legacy = replay([a, b], ReplayConfig(initial_cash=110, reserve_cash_at_send=True))
        self.assertEqual(stressed["counts"]["full_fills"], 1)
        self.assertEqual(legacy["counts"]["full_fills"], 2)
        self.assertEqual(stressed["counts"]["send_cash_rejects"], 1)
        self.assertAlmostEqual(stressed["acknowledgement"]["max_pending_cash"], 20.07)

    def test_reuse_only_after_ack(self):
        r = replay([order(), order("b", send=401, arrival=500, ack=600)], config())
        self.assertEqual(r["counts"]["full_fills"], 2)
        self.assertEqual(r["acknowledgement"]["count"], 2)

    def test_cash_restriction_is_not_a_pnl_lower_bound(self):
        orders = [order(), order("b", send=200, arrival=300, ack=600, side="NO")]
        stressed = replay(orders, config())
        legacy = replay(orders, ReplayConfig(initial_cash=110, reserve_cash_at_send=True))
        self.assertGreater(stressed["cash"]["net_pnl"], legacy["cash"]["net_pnl"])

    def test_equal_clock_send_precedes_ack_conservatively(self):
        r = replay([order(), order("b", send=400, arrival=500, ack=600)], config())
        self.assertEqual(r["counts"]["send_cash_rejects"], 1)
        self.assertEqual([x["event"] for x in r["ledger"] if x["timestamp_ms"] == 400],
                         ["order_send", "order_ack"])

    def test_zero_fill_keeps_full_reserve_until_ack(self):
        for kwargs in ({"ask": .7}, {"depth": 0}):
            with self.subTest(kwargs=kwargs):
                r = replay([order(**kwargs), order("b", send=200, arrival=300, ack=600)], config())
                self.assertEqual(r["counts"]["fills"], 0)
                self.assertAlmostEqual(r["acknowledgement"]["max_pending_cash"], 61.75)
                self.assertEqual(r["counts"]["send_cash_rejects"], 1)

    def test_partial_fak_retains_unused_reserve(self):
        r = replay([order(depth=20)], config())
        arrival = next(x for x in r["ledger"] if x["event"] == "order_arrival")
        self.assertEqual(arrival["filled_shares"], 20)
        self.assertAlmostEqual(arrival["reservation_consumed"], 8.336)
        self.assertAlmostEqual(arrival["reservation_retained_until_ack"], 53.414)
        self.assertEqual(arrival["reservation_released"], 0)
        self.assertAlmostEqual(r["cash"]["net_pnl"], 11.664)

    def test_fok_failure_does_not_release_at_arrival(self):
        r = replay([order(depth=20)], config(full_or_zero=True))
        self.assertEqual(r["counts"]["fills"], 0)
        self.assertAlmostEqual(r["acknowledgement"]["max_pending_cash"], 61.75)
        self.assertEqual(r["cash"]["final"], 110)

    def test_ack_after_settlement_does_not_reopen_inventory(self):
        r = replay([order(ack=6000)], config())
        self.assertEqual(r["risk"]["total_unhedged_ms"], 4900)
        self.assertEqual(r["ledger"][-1]["event"], "order_ack")
        self.assertEqual(r["acknowledgement"]["final_pending_orders"], 0)
        self.assertEqual(r["cash"]["reserved_final"], 0)

    def test_same_time_match_then_ack(self):
        r = replay([order(ack=100)], config())
        self.assertEqual([x["event"] for x in r["ledger"] if x["timestamp_ms"] == 100],
                         ["order_arrival", "order_ack"])

    def test_malformed_or_missing_ack_fails_closed(self):
        for value in (None, True, "400", 400.5, -1, 99):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replay([dataclasses.replace(order(), ack_ms=value)], config())
        for value in (0, 1, "false", None):
            with self.subTest(flag=value), self.assertRaises(ValueError):
                replay([], ReplayConfig(reserve_until_ack=value))
        with self.assertRaises(ValueError):
            replay([], ReplayConfig(reserve_until_ack=True))

    def test_snapshot_pool_and_extra_cost_compose(self):
        a = dataclasses.replace(order(), snapshot_id="s")
        b = dataclasses.replace(order("b", send=401, arrival=500, ack=600), snapshot_id="s")
        r = replay([a, b], config(consume_snapshot_depth=True, extra_cost_per_share=.01))
        self.assertEqual(r["snapshot_depth"]["consumed_shares"], 100)
        self.assertEqual(r["counts"]["fills"], 1)
        self.assertAlmostEqual(r["cash"]["net_pnl"], 57.32)

    def test_input_permutation_does_not_change_ledger(self):
        orders = [order(), order("b", send=401, arrival=500, ack=600)]
        self.assertEqual(replay(orders, config()), replay(reversed(orders), config()))

    def test_random_cash_and_reservation_conservation(self):
        rng = random.Random(20261020)
        for trial in range(100):
            orders = [order(str(i), send=i*20, arrival=i*20+10,
                            ack=i*20+rng.randrange(10, 300), ask=rng.choice((.3,.5,.7)),
                            depth=rng.choice((0,20,100))) for i in range(10)]
            r = replay(orders, config())
            cash, reserved = 110., 0.
            for row in r["ledger"]:
                if "cash_after" in row:
                    self.assertAlmostEqual(row["cash_before"], cash)
                    cash = row["cash_after"]
                if "reserved_after" in row:
                    self.assertAlmostEqual(row["reserved_before"], reserved)
                    reserved = row["reserved_after"]
                self.assertGreaterEqual(cash-reserved, -1e-8)
            self.assertAlmostEqual(reserved, 0)
            self.assertTrue(math.isfinite(r["cash"]["net_pnl"]))


if __name__ == "__main__":
    unittest.main()
