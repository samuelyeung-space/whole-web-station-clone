"""Synthetic fee/accounting checks, not historical or OOS validation."""
import dataclasses
import contextlib
import io
import json
import random
import unittest
from unittest.mock import patch

from paired_leg_inventory import LegIntent, MarketFee, ReplayConfig, main, replay, taker_fee


def order(oid="a", market="m", **changes):
    base = LegIntent(oid, oid, market, "YES", 1000, 1500, 5000, 5500,
                     True, 100, .6, .45, 100, 1100, "s", 2000)
    return dataclasses.replace(base, **changes)


def fee(market="m", rate=.07, **changes):
    base = MarketFee(market, rate, 500, 5000, 900, "synthetic://fixture", "a"*64)
    return dataclasses.replace(base, **changes)


class MarketFeeTests(unittest.TestCase):
    def test_cli_never_silently_ignores_inline_market_fees(self):
        with patch("sys.argv", ["replay", "intents.json"]), \
             patch("pathlib.Path.read_text", return_value='{"intents":[],"market_fees":[]}'), \
             self.assertRaisesRegex(ValueError, "use --market-fees"):
            main()

    def test_cli_explicit_fee_file(self):
        payload = json.dumps({"intents":[dataclasses.asdict(order())]})
        proof = json.dumps([dataclasses.asdict(fee())])
        output = io.StringIO()
        with patch("sys.argv", ["replay", "intents.json", "--market-fees", "fees.json"]), \
             patch("pathlib.Path.read_text", side_effect=[payload, proof]), \
             contextlib.redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 9)

    def test_cli_rejects_non_array_fee_input(self):
        for proof in ({}, None, [False]):
            with self.subTest(proof=proof), \
                 patch("sys.argv", ["replay", "intents.json", "--market-fees", "fees.json"]), \
                 patch("pathlib.Path.read_text", side_effect=["{\"intents\":[]}", json.dumps(proof)]), \
                 self.assertRaises(ValueError):
                main()

    def test_independent_market_rates_and_no_global_fallback(self):
        r = replay([order(), order("b", "n")], ReplayConfig(fee_rate=.8),
                   market_fees=[fee(), fee("n", 0)])
        self.assertEqual([x["fee"] for x in r["fills"]], [1.7325, 0])
        self.assertEqual(r["schema_version"], 9)
        self.assertFalse(r["market_fees"]["config_fee_rate_applied"])
        self.assertAlmostEqual(r["cash"]["final"], 1108.2675)

    def test_partial_fee_rounding_remains_per_order(self):
        r = replay([order(displayed_shares=5)], market_fees=[fee()])
        self.assertEqual(r["fills"][0]["fee"], .08663)
        self.assertEqual(r["counts"]["partial_fills"], 1)

    def test_own_rate_sets_send_reservation(self):
        c = ReplayConfig(initial_cash=61, reserve_cash_at_send=True)
        paid = replay([order()], c, market_fees=[fee()])
        free = replay([order()], c, market_fees=[fee(rate=0)])
        self.assertEqual(paid["counts"]["send_cash_rejects"], 1)
        self.assertEqual(free["counts"]["full_fills"], 1)
        send = next(x for x in free["ledger"] if x["event"] == "order_send")
        self.assertEqual(send["reservation"], 60)
        self.assertEqual(send["fee_rate"], 0)

    def test_two_legs_share_one_market_fee_and_audit(self):
        a = order(pair_id="p")
        b = order("b", pair_id="p", side="NO", arrival_ms=1600)
        r = replay([a, b], market_fees=[fee(rate=.02)])
        self.assertEqual(r["counts"]["pairs_with_both_sides_filled"], 1)
        self.assertEqual([x["fee"] for x in r["fills"]], [.495, .495])
        for row in r["ledger"]:
            if row["event"] == "order_arrival":
                self.assertEqual(row["fee_evidence_sha256"], "a"*64)
                self.assertEqual(row["fee_rate"], .02)

    def test_exact_market_coverage_no_missing_extra_or_duplicate(self):
        for records in ([], [fee("other")], [fee(), fee()], [fee(), fee("other")]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                replay([order()], market_fees=records)

    def test_caller_cannot_pass_untyped_record(self):
        with self.assertRaises(ValueError):
            replay([order()], market_fees=[dataclasses.asdict(fee())])

    def test_no_late_or_equal_clock_observation(self):
        for at in (1000, 1001, 1500):
            with self.subTest(at=at), self.assertRaises(ValueError):
                replay([order()], market_fees=[fee(observed_ms=at)])
        replay([order()], market_fees=[fee(observed_ms=999)])

    def test_half_open_effective_bounds(self):
        for changes in ({"effective_from_ms":1001}, {"effective_until_ms":1500},
                        {"effective_until_ms":1000}, {"effective_until_ms":500}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replay([order()], market_fees=[fee(**changes)])
        replay([order()], market_fees=[fee(effective_from_ms=1000,
                                          effective_until_ms=1501)])

    def test_metadata_must_cover_every_order_not_only_first(self):
        with self.assertRaises(ValueError):
            replay([order(), order("b", decision_ms=2000, send_ms=2100,
                                   arrival_ms=2500, ack_ms=3000)],
                   market_fees=[fee(effective_until_ms=2000)])

    def test_strict_fee_rate_and_timestamp_types(self):
        for rate in (True, "0.07", float("nan"), float("inf"), -.01, 1.01):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                replay([order()], market_fees=[fee(rate=rate)])
        for key in ("effective_from_ms", "effective_until_ms", "observed_ms"):
            for value in (True, "900", 900.5, -1, None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    replay([order()], market_fees=[fee(**{key:value})])

    def test_source_identity_required_not_invented(self):
        for changes in ({"source_ref":""}, {"source_ref":"   "},
                        {"source_ref":False}, {"evidence_sha256":"a"*63},
                        {"evidence_sha256":"g"*64}, {"evidence_sha256":None},
                        {"market_id":""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replay([order()], market_fees=[fee(**changes)])

    def test_zero_fak_and_full_or_zero_charge_no_fee(self):
        for changes, config in (({"displayed_shares":0}, ReplayConfig()),
                                ({"arrival_ask":.7}, ReplayConfig()),
                                ({"displayed_shares":5}, ReplayConfig(full_or_zero=True))):
            r = replay([order(**changes)], config, market_fees=[fee()])
            arrival = next(x for x in r["ledger"] if x["event"] == "order_arrival")
            self.assertEqual(arrival["fee"], 0)
            self.assertEqual(arrival["fee_rate"], .07)
            self.assertEqual(r["counts"]["zero_fills"], 1)

    def test_ack_and_shared_depth_compose_without_double_charging(self):
        c = ReplayConfig(reserve_cash_at_send=True, reserve_until_ack=True,
                         consume_snapshot_depth=True, depth_haircut=.5,
                         extra_cost_per_share=.01)
        r = replay([order(), order("b", arrival_ms=1600)], c, market_fees=[fee(rate=.02)])
        self.assertEqual(r["counts"]["fills"], 1)
        self.assertEqual(r["snapshot_depth"]["consumed_shares"], 50)
        self.assertEqual(r["acknowledgement"]["count"], 2)
        self.assertEqual(r["cash"]["reserved_final"], 0)
        self.assertAlmostEqual(r["fills"][0]["cash_debit"], 23.2475)

    def test_record_input_order_does_not_change_complete_output(self):
        intents = [order(), order("b", "n")]
        records = [fee(), fee("n", .02)]
        self.assertEqual(replay(intents, market_fees=records),
                         replay(reversed(intents), market_fees=reversed(records)))

    def test_random_market_rates_cash_and_reservation_conservation(self):
        rng = random.Random(20261020)
        c = ReplayConfig(reserve_cash_at_send=True, reserve_until_ack=True)
        for _ in range(100):
            orders = [order(str(i), str(i), arrival_ask=rng.choice((.1,.45,.7)),
                            displayed_shares=rng.choice((0,5,100))) for i in range(10)]
            records = [fee(str(i), rng.choice((0,.02,.07,.2,1))) for i in range(10)]
            r = replay(orders, c, market_fees=records)
            for fill in r["fills"]:
                rate = records[int(fill["market_id"])].fee_rate
                self.assertEqual(fill["fee"], taker_fee(fill["filled_shares"], fill["price"], rate))
            for row in r["ledger"]:
                if "reserved_after" in row:
                    cash = row.get("cash_after", row.get("cash"))
                    self.assertGreaterEqual(cash-row["reserved_after"], -1e-8)
            self.assertEqual(r["cash"]["reserved_final"], 0)
            self.assertAlmostEqual(r["cash"]["final"], c.initial_cash
                - sum(f["cash_debit"] for f in r["fills"])
                + sum(f["filled_shares"] for f in r["fills"]))


if __name__ == "__main__":
    unittest.main()
