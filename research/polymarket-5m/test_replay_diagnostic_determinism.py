"""Missing-input attribution must reproduce across independent Python processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


HERE = Path(__file__).resolve().parent


class DiagnosticDeterminismTests(unittest.TestCase):
    def test_multiple_missing_features_have_same_first_reason_across_hash_seeds(self):
        script = '''
import json
from replay_engine import replay_events, ReplayConfig
events = [
    {"type":"dataset", "recv_ms":0, "dataset_id":"synthetic-hash-seed",
     "split":"development", "provenance":{"source":"synthetic-test-not-backtest"}},
    {"type":"market", "recv_ms":0, "market_id":"m", "start_ms":0,
     "end_ms":300000, "label_source":"gamma"},
    {"type":"decision", "recv_ms":100, "market_id":"m",
     "features":{"tau_s":299.9}, "feature_available_ms":{"tau_s":100}}
]
def forbidden_signal(candidate, features):
    raise AssertionError("missing features must never reach strategy")
r = replay_events(events, [{"id":"missing-two", "required_features":["z_missing","a_missing"]}],
                  forbidden_signal, ReplayConfig(latencies_ms=(500,)))
print(json.dumps({k:r[k] for k in ("summary", "attempts", "cash_events")}, sort_keys=True))
'''
        results = []
        for seed in ('0', '1'):
            run = subprocess.run([sys.executable, '-c', script], cwd=HERE,
                env={**os.environ, 'PYTHONHASHSEED': seed}, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            results.append(json.loads(run.stdout))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['summary'][0]['invalid_decision_reasons'],
                         {'missing_or_invalid_feature:a_missing': 1})
        self.assertEqual(results[0]['attempts'], [])
        self.assertEqual(results[0]['cash_events'], [])
        self.assertIsNone(results[0]['summary'][0]['net_pnl_usd'])


if __name__ == '__main__':
    unittest.main()
