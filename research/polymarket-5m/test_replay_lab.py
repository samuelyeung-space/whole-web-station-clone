"""CLI integration acceptance: hand-computable synthetic data, not market results."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil


HERE = Path(__file__).resolve().parent


class ReplayLabCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plugin = self.root / "hand_strategy.py"
        self.plugin.write_text("def catalog():\n    return [{'id':'HAND-1','required_features':['x']}]\n"
            "def signal(c, f):\n    return 'Up' if f['x'] > 0 else None\n")
        self.tape = self.root / "hand.jsonl"
        self.write_tape()

    def tearDown(self):
        self.tmp.cleanup()

    def write_tape(self, observed_end=300000):
        rows = [
            {"type": "dataset", "recv_ms": 0, "dataset_id": "hand-computed", "split": "development",
             "provenance": {"source": "synthetic_acceptance_not_backtest"},
             "coverage_start_ms": 0, "coverage_end_ms": observed_end},
            {"type": "market", "recv_ms": 0, "market_id": "m", "start_ms": 0,
             "end_ms": 300000, "label_source": "gamma"},
            {"type": "constraint", "recv_ms": 0, "market_id": "m", "min_order_size": 5,
             "up_tick_size": .01, "down_tick_size": .01, "constraints_source": "gamma"},
            {"type": "book", "recv_ms": 100, "market_id": "m", "up_asks": [[.4, 100]],
             "down_asks": [[.6, 100]], "healthy": True, "fee_rate": .07},
            {"type": "decision", "recv_ms": 100, "market_id": "m",
             "features": {"tau_s": 299.9, "x": 1}, "feature_available_ms": {"tau_s": 100, "x": 100}},
            {"type": "outcome", "recv_ms": 300000, "market_id": "m", "resolved_ms": 300000,
             "winner": "Up", "source": "gamma"},
        ]
        self.tape.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def cli(self, *args, expected=0):
        run = subprocess.run([sys.executable, str(HERE / "replay_lab.py"), *map(str, args)],
            cwd=HERE, text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, expected, msg=run.stdout + "\n" + run.stderr)
        stream = run.stdout if expected == 0 else run.stderr
        return json.loads(stream.strip().splitlines()[-1])

    def pipeline(self, *extra, expected=0):
        return self.cli("pipeline", "--input", self.tape, "--cache-root", self.root / "cache",
            "--out", self.root / "runs", "--plugin-dir", self.root,
            "--strategy", "hand_strategy:HAND-1", "--shares", "100", "--max-quote-age-ms", "1500",
            *extra, expected=expected)

    def test_pipeline_hand_computed_pnl_audit_registration_and_exact_repeat_reuse(self):
        first = self.pipeline()
        self.assertFalse(first["cache_hit"])
        self.assertEqual(first["new_backtests"], 3)
        target = Path(first["run_path"])
        registration = json.loads((target / "registration.json").read_text())
        self.assertEqual(registration["strategies"]["candidate_count"], 1)
        self.assertFalse(registration["model_fitting"])
        self.assertIsNone(registration["formal_oos_result"])
        rows = json.loads((target / "metrics.json").read_text())
        self.assertEqual({row["latency_ms"] for row in rows}, {500, 600, 1000})
        for row in rows:
            self.assertEqual(row["attempts"], 1)
            self.assertEqual(row["full_fills"], 1)
            self.assertEqual(row["filled_shares"], 100)
            self.assertAlmostEqual(row["fees_usd"], 1.68)
            self.assertAlmostEqual(row["net_pnl_usd"], 58.32)
            self.assertAlmostEqual(row["ending_cash_usd"], 1058.32)
        before = (target / "registration.json").read_bytes()
        again = self.pipeline()
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["new_backtests"], 0)
        self.assertEqual(again["run_path"], first["run_path"])
        self.assertEqual((target / "registration.json").read_bytes(), before)
        self.assertEqual(len(list((self.root / "runs").glob("*/completion.json"))), 1)

    def test_seven_day_request_fails_with_null_and_retains_registration(self):
        error = self.pipeline("--require-full-days", "7", expected=2)
        self.assertIsNone(error["pnl"])
        self.assertIn("requires 7", error["error"])
        failures = list((self.root / "runs").glob("*.pending.*/failed.json"))
        self.assertEqual(len(failures), 1)
        failed = json.loads(failures[0].read_text())
        self.assertIsNone(failed["net_pnl_usd"])
        self.assertTrue(failures[0].with_name("registration.json").exists())
        self.assertFalse(list((self.root / "runs").glob("*/completion.json")))

    def test_seven_day_claimed_span_without_market_data_does_not_pass_gate(self):
        # Declaring seven days in a header does not create 2016 covered markets.
        self.write_tape(observed_end=7 * 86400000)
        error = self.pipeline("--require-full-days", "7", expected=2)
        self.assertIsNone(error["pnl"])
        self.assertIn("requires 7", error["error"])
        self.assertFalse(list((self.root / "runs").glob("*/completion.json")))

    def test_same_plugin_bytes_moved_directory_reuse_run_identity(self):
        first = self.pipeline()
        moved = self.root / "relocated_plugins"; moved.mkdir()
        shutil.copy2(self.plugin, moved / self.plugin.name)
        self.plugin.unlink()
        again = self.pipeline("--plugin-dir", moved)
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["new_backtests"], 0)
        self.assertEqual(again["run_key"], first["run_key"])

    def test_same_rules_new_cost_assumption_create_new_scenario_identity(self):
        first = self.pipeline()
        second = self.pipeline("--stresses", "plus_1cent")
        self.assertNotEqual(first["run_key"], second["run_key"])
        rows = json.loads((Path(second["run_path"]) / "metrics.json").read_text())
        self.assertTrue(all(abs(row["net_pnl_usd"] - 57.32) < 1e-9 for row in rows))

    def test_corrupt_completed_output_is_not_reused(self):
        first = self.pipeline()
        (Path(first["run_path"]) / "metrics.json").write_text("[]")
        error = self.pipeline(expected=2)
        self.assertIn("corrupted", error["error"])
        self.assertIsNone(error["pnl"])

    def test_normalized_input_missing_real_schema_metadata_fails_before_backtest(self):
        rows = [json.loads(line) for line in self.tape.read_text().splitlines()]
        rows[0].pop("provenance")
        self.tape.write_text("".join(json.dumps(row) + "\n" for row in rows))
        error = self.pipeline(expected=2)
        self.assertIn("provenance", error["error"])
        self.assertIsNone(error["pnl"])
        self.assertFalse((self.root / "runs").exists())


if __name__ == "__main__":
    unittest.main()
