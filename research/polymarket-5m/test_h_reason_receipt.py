"""Synthetic preservation checks against the pinned real summary function."""
import ast
import copy
import hashlib
from collections import Counter
from pathlib import Path
import unittest
from typing import Any
from h_reason_accounting import summarize_with_receipt

PIN = "1d15596f4b63b78b2e843b112040e4842b961a41e158efcdbd2ebea1d891f2c4"

def frozen_summary():
    source = Path(__file__).with_name("h_replay_run.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != PIN:
        raise ValueError("summary source drift")
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ("summarize", "_exact_pvalue")]
    namespace = dict(Counter=Counter, Any=Any)
    exec(compile(ast.Module(body=functions, type_ignores=[]), "pinned_summary", "exec"), namespace)
    return namespace["summarize"]

class ReasonReceiptTests(unittest.TestCase):
    def fixture(self):
        rows = [dict(evaluation_ms=500, reason="filled", signal_source="spot_trade",
            filled=True, winner="Up", sent=True, filled_shares=5., pnl=1.25,
            day="synthetic-only", won=True, all_in_cost=.75)]
        counters = dict(events=20, _suppressed_unsent=[
            dict(evaluation_ms=500, reason="rate", source="spot_trade", count=7),
            dict(evaluation_ms=500, reason="rate", source="deribit_quote", count=11)])
        return rows, counters

    def test_pinned_real_summary_collision_is_preserved_but_receipt_is_complete(self):
        rows, counters = self.fixture()
        old_rows, old_counters = copy.deepcopy(rows), copy.deepcopy(counters)
        original = frozen_summary(); expected = original(rows, counters); receipts=[]
        actual = summarize_with_receipt(original, rows, counters, sink=receipts.append)
        self.assertEqual(actual, expected)
        self.assertEqual((rows, counters), (old_rows, old_counters))
        self.assertNotIn("_suppressed_unsent", actual["counters"])
        self.assertEqual(actual["latencies"]["500"]["reasons"]["rate"], 11)
        r = receipts[0]
        self.assertEqual(r["pre_compaction_counters"], old_counters)
        b = r["reason_accounting"]["by_latency"]["500"]
        self.assertEqual(b["reasons"]["rate"], 18)
        self.assertEqual(b["signals"], 19)
        self.assertEqual(b["legacy_missing_count"], 7)
        self.assertFalse(r["economic_admission"])
        counters["_suppressed_unsent"][0]["count"] = 999
        self.assertEqual(r["pre_compaction_counters"], old_counters)

    def test_explicit_counter_argument_takes_precedence_and_remains_unchanged(self):
        rows, counters = self.fixture(); receipts=[]
        explicit = Counter({(500., "rate", "spot_trade"): 3,
                            (500., "rate", "deribit_quote"): 8})
        before = explicit.copy(); original=frozen_summary()
        expected = original(rows, counters, explicit)
        actual = summarize_with_receipt(original, rows, counters, explicit,
            sink=receipts.append)
        self.assertEqual(actual, expected); self.assertEqual(explicit, before)
        self.assertEqual(receipts[0]["reason_accounting"]["by_latency"]["500"]["signals"], 12)

    def test_missing_buckets_fail_before_export(self):
        rows, counters = self.fixture(); result=frozen_summary()(rows,counters); receipts=[]
        with self.assertRaisesRegex(ValueError, "complete rows/counters"):
            summarize_with_receipt(lambda *_:result, rows, {}, sink=receipts.append)
        self.assertEqual(receipts, [])

    def test_invalid_count_is_not_silently_coerced_by_legacy_summary(self):
        rows,counters=self.fixture();counters["_suppressed_unsent"][0]["count"]=True
        receipts=[]
        with self.assertRaises(ValueError):
            summarize_with_receipt(frozen_summary(),rows,counters,sink=receipts.append)
        self.assertEqual(receipts,[])

    def test_empty_sample_stays_empty(self):
        receipts=[]; original=frozen_summary()
        actual=summarize_with_receipt(original,[],{},sink=receipts.append)
        self.assertEqual(actual, original([],{}))
        self.assertEqual(actual["latencies"],{})
        self.assertEqual(receipts[0]["reason_accounting"]["by_latency"],{})

if __name__ == "__main__":
    unittest.main()
