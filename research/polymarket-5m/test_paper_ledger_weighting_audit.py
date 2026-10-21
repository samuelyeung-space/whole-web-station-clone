import csv
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).with_name("paper_ledger_weighting_audit.py")
SPEC = importlib.util.spec_from_file_location("paper_weight", PATH)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


def write_ledger(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["slug", "tau", "shares", "cost", "won", "pnl"])
        writer.writeheader()
        writer.writerows(rows)


class WeightingAuditTests(unittest.TestCase):
    def test_share_weighted_result_differs_from_equal_trade_weight(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.csv"
            write_ledger(
                path,
                [
                    {"slug": "btc-a", "tau": 60, "shares": 100, "cost": 50, "won": "True", "pnl": 50},
                    {"slug": "btc-b", "tau": 60, "shares": 10, "cost": 9, "won": "False", "pnl": -9},
                ],
            )
            audit = MOD.audit(path)
            row = audit["groups"][0]
            self.assertEqual(row["net_pnl_usd"], 41)
            self.assertTrue(math.isclose(row["share_weighted_net_pnl_per_share_usd"], 41 / 110))
            self.assertTrue(math.isclose(row["equal_trade_weighted_mean_pnl_per_share_usd"], -0.2))
            self.assertFalse(audit["financial_replay_performed"])
            self.assertIsNone(audit["rank7_result"])

    def test_invalid_rows_fail_closed(self):
        for field, value in (("shares", "0"), ("shares", "nan"), ("cost", "-1"), ("pnl", "inf"), ("won", "yes")):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "ledger.csv"
                row = {"slug": "btc-a", "tau": 60, "shares": 5, "cost": 2, "won": "True", "pnl": 3}
                row[field] = value
                write_ledger(path, [row])
                with self.assertRaises(ValueError):
                    MOD.audit(path)

    def test_duplicate_market_horizon_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.csv"
            row = {"slug": "btc-a", "tau": 60, "shares": 5, "cost": 2, "won": "True", "pnl": 3}
            write_ledger(path, [row, row])
            with self.assertRaises(ValueError):
                MOD.audit(path)


if __name__ == "__main__":
    unittest.main()
