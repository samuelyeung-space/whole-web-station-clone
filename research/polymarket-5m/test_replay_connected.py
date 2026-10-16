"""Synthetic interface tests; real-data acceptance is recorded separately."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import replay_connected as connected

KEY = 'a' * 64


class ConnectedTests(unittest.TestCase):
    def args(self, **changes):
        values = dict(strategy=['mine500_catalog:M500-439'], shared=False, out='test-output',
                      fill_policy='partial-fak', require_full_days=0, plugin_dir=[],
                      accept_legacy_assumptions=False)
        return SimpleNamespace(**{**values, **changes})

    def test_fixed_benchmark(self):
        cmd = connected.command_for(self.args(), Path(KEY), {'dataset_header': {}})
        self.assertEqual(cmd[cmd.index('--shares') + 1], '100')
        self.assertEqual(cmd[cmd.index('--cash') + 1], '1000')
        self.assertNotIn('--full-or-zero', cmd)
        self.assertEqual(cmd[cmd.index('--latencies-ms') + 1], '500,600,1000')

    def test_legacy_is_explicit(self):
        m = {'dataset_header': {'strict_execution_admitted': False}}
        with self.assertRaisesRegex(ValueError, 'legacy'):
            connected.command_for(self.args(), Path(KEY), m)
        cmd = connected.command_for(self.args(accept_legacy_assumptions=True), Path(KEY), m)
        self.assertIn('--side-specific-books', cmd)

    def test_shared_catalog_forbidden(self):
        with self.assertRaisesRegex(ValueError, 'shared cash'):
            connected.command_for(self.args(shared=True, strategy=['mine500_catalog:*']), Path(KEY), {'dataset_header': {}})

    def test_catalog_is_sharded(self):
        cmd = connected.command_for(self.args(strategy=['mine500_catalog:*']), Path(KEY), {'dataset_header': {}})
        self.assertTrue(cmd[1].endswith('replay_catalog_batch.py'))
        self.assertIn('--batch-size', cmd)

    def test_full_or_zero_is_explicit_stress(self):
        cmd = connected.command_for(self.args(fill_policy='full-or-zero'), Path(KEY), {'dataset_header': {}})
        self.assertIn('--full-or-zero', cmd)

    def test_partial_fak_separate_identity(self):
        cmd = connected.command_for(self.args(fill_policy='partial-fak'), Path(KEY), {'dataset_header': {}})
        self.assertNotIn('--full-or-zero', cmd)
        self.assertEqual(cmd[cmd.index('--shares') + 1], '100')

    def test_resolves_only_exact_unique_dataset(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); cache = root / 'nested' / KEY; cache.mkdir(parents=True)
            (cache / 'manifest.json').write_text('{}')
            self.assertEqual(connected.resolve_cache(root, KEY), cache)
            other = root / 'other' / KEY; other.mkdir(parents=True); (other / 'manifest.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'found 2'):
                connected.resolve_cache(root, KEY)

    def test_invalid_and_missing_key(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError, 'exact lowercase'):
                connected.resolve_cache(t, '../bad')
            with self.assertRaisesRegex(ValueError, 'found 0'):
                connected.resolve_cache(t, KEY)


if __name__ == '__main__':
    unittest.main()

