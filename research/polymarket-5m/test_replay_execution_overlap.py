"""Hand-computed diagnostic checks only; no market backtest."""
import unittest

from replay_execution_overlap import execution_overlap


class ExecutionOverlapTests(unittest.TestCase):
    def row(self, candidate, latency=500, market="m", quantity=5, attempt="1"):
        return {"candidate_id": candidate, "attempt_id": attempt, "latency_ms": latency,
                "market_id": market, "side": "Up", "filled_shares": quantity,
                "decision_ms": 1000, "status": "filled", "fees_usd": .08}

    def test_ids_ignored_but_economic_difference_retained(self):
        rows = [self.row("A", attempt="1"), self.row("B", attempt="2"), self.row("C", quantity=4)]
        out = execution_overlap(rows, [(name,500) for name in ["A","B","C","D"]])[0]
        self.assertEqual(out["distinct_nonempty_observed_attempt_sequences"],2)
        self.assertEqual(out["zero_attempt_candidates"],["D"])
        self.assertIn(["A","B"],[g["candidate_ids"] for g in out["exact_nonempty_sequence_groups"]])
        self.assertEqual(out["registered_candidates"],4)

    def test_latency_accounts_remain_separate(self):
        out=execution_overlap([self.row("A",500),self.row("A",1000)],[("A",500),("A",1000)])
        self.assertEqual([r["latency_ms"] for r in out],[500,1000])
        self.assertTrue(all(r["registered_candidates"]==1 for r in out))

    def test_equal_positions_different_order_is_not_exact_sequence(self):
        rows=[self.row("A",market="m"),self.row("A",market="n"),
              self.row("B",market="n"),self.row("B",market="m")]
        out=execution_overlap(rows,[("A",500),("B",500)])[0]
        self.assertEqual(out["distinct_nonempty_observed_attempt_sequences"],2)
        self.assertEqual(out["highest_filled_market_side_overlap_pairs"][0]["jaccard"],1)

    def test_unregistered_attempt_rejected(self):
        with self.assertRaisesRegex(ValueError,"absent from metrics"):
            execution_overlap([self.row("B")],[("A",500)])


if __name__ == "__main__":
    unittest.main()
