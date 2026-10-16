"""Portable launcher acceptance; all market events here are synthetic."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import replay_cli

HERE = Path(__file__).resolve().parent


class PortableReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, expected=0):
        p = subprocess.run([sys.executable, str(HERE/'replay_cli.py'), *map(str, args)],
                           cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, expected, p.stdout + '\n' + p.stderr)
        return json.loads((p.stdout if expected == 0 else p.stderr).strip().splitlines()[-1])

    def test_doctor_ready_without_historical_claim(self):
        result = self.cli('doctor')
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['historical_coverage_verified'])
        self.assertFalse(result['network_or_orders'])

    def test_demo_hand_counts_and_repeat_cache(self):
        first = self.cli('demo', '--work', self.root/'demo')
        second = self.cli('demo', '--work', self.root/'demo')
        self.assertEqual(first['registered_markets'], 6)
        self.assertTrue(first['synthetic_only'])
        self.assertIsNone(first['historical_performance'])
        self.assertFalse(first['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(second['new_backtests'], 0)
        self.assertEqual(first['run_path'], second['run_path'])
        for key in ('full_fills_per_scenario', 'partial_fills_per_scenario',
                    'no_fills_per_scenario', 'stale_rejections_per_scenario'):
            self.assertEqual(first[key], 1)

    def test_refuses_to_overwrite_user_changes(self):
        work = self.root/'demo'
        self.cli('demo', '--work', work)
        path = work/'portable_demo_strategy.py'
        path.write_text('# user changes\n')
        error = self.cli('demo', '--work', work, expected=2)
        self.assertIn('refusing to overwrite', error['error'])
        self.assertEqual(path.read_text(), '# user changes\n')
        self.assertIsNone(error['pnl'])

    def test_input_run_defaults_from_another_working_directory(self):
        work = self.root/'demo'
        self.cli('demo', '--work', work)
        result = self.cli('run', '--input', work/'synthetic.jsonl', '--plugin-dir', work,
                          '--strategy', 'portable_demo_strategy:DEMO-TAKER')
        self.assertEqual(Path(result['run_path']).parent, self.root/'replay-runs')
        self.assertTrue((self.root/'.replay-cache'/'datasets').is_dir())
        self.assertEqual(result['candidate_count'], 1)

    def test_equals_options_preserve_user_destinations(self):
        work = self.root/'demo'
        self.cli('demo', '--work', work)
        result = self.cli('run', '--input='+str(work/'synthetic.jsonl'), '--plugin-dir', work,
                          '--strategy', 'portable_demo_strategy:DEMO-TAKER',
                          '--cache-root='+str(self.root/'custom-cache'),
                          '--out='+str(self.root/'custom-runs'))
        self.assertEqual(Path(result['run_path']).parent, self.root/'custom-runs')
        self.assertTrue((self.root/'custom-cache'/'datasets').is_dir())
        self.assertFalse((self.root/'replay-runs').exists())

    def test_conflicting_sources_fail_with_null_pnl(self):
        error = self.cli('run', '--input', 'a.zip', '--cache', 'cache', expected=2)
        self.assertIn('choose', error['error'])
        self.assertIsNone(error['pnl'])

    def test_bad_command_does_not_start_replay(self):
        error = self.cli('trade', expected=2)
        self.assertIn('unknown command', error['error'])
        self.assertFalse((self.root/'replay-runs').exists())

    def test_generated_tape_sorted_and_all_market_cases_retained(self):
        rows = replay_cli.demo_rows()
        clocks = [r['recv_ms'] for r in rows]
        self.assertEqual(clocks, sorted(clocks))
        markets = [r['market_id'] for r in rows if r['type'] == 'market']
        self.assertEqual(len(markets), 6)
        self.assertIn('demo-no_signal', markets)
        self.assertIn('demo-missing_feature', markets)
        self.assertEqual(rows[0]['provenance']['source'], 'SYNTHETIC_ACCEPTANCE_ONLY')


if __name__ == '__main__':
    unittest.main()
