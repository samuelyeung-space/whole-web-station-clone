"""Run after applying dependency-snapshot integration to runner/lab/batch."""
import importlib
import json
import sys
import unittest

import replay_runner as runner
import test_replay_lab as lab_tests


class PluginClosureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lab_tests.ReplayLabCliTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.helper = self.root / 'closure_fixture_runtime.py'
        self.helper.write_text("SIDE='Up'\n")
        self.fixture.plugin.write_text('from closure_fixture_runtime import SIDE\n'
            "def catalog():\n    return [{'id':'HAND-1','required_features':['x']}]\n"
            'def signal(c, f):\n    return SIDE\n')

    def tearDown(self):
        self.fixture.tearDown()

    def test_only_helper_changes_new_cache_key_and_actual_signal(self):
        first = self.fixture.pipeline()
        self.helper.write_text('SIDE=None\n')
        second = self.fixture.pipeline()
        self.assertNotEqual(first['run_key'], second['run_key'])
        self.assertFalse(second['cache_hit'])
        from pathlib import Path
        rows = json.loads((Path(second['run_path']) / 'metrics.json').read_text())
        self.assertTrue(all(row['attempts'] == 0 for row in rows))
        third = self.fixture.pipeline()
        self.assertTrue(third['cache_hit'])
        self.assertEqual(third['new_backtests'], 0)
        registered = json.loads((Path(second['run_path']) / 'registration.json').read_text())
        self.assertIn('closure_fixture_runtime', registered['strategies']
                      ['plugin_dependency_identity']['hand_strategy']['module_sha256'])
        self.assertNotIn('plugin_dependency_paths', registered['strategies'])

    def test_helper_edit_after_registration_rejected_before_events_consumed(self):
        sys.path.insert(0, str(self.root))
        try:
            importlib.invalidate_caches()
            loaded = runner.load_strategies(['hand_strategy:*'])
            self.helper.write_text('SIDE=None\n')
            def events():
                raise AssertionError('events should never be consumed')
                yield
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                runner.execute(events(), loaded)
        finally:
            sys.path.remove(str(self.root))


    def test_sharder_rejects_helper_change_between_shards(self):
        from unittest import mock
        import replay_lab as lab
        import test_replay_catalog_batch as batch_tests
        fixture = batch_tests.CatalogBatchTests()
        fixture.setUp()
        try:
            helper = fixture.root / 'closure_fixture_batch_dep.py'
            helper.write_text("SIDE='Up'\n")
            plugin = fixture.root / 'shard_fixture_plugin.py'
            plugin.write_text('from closure_fixture_batch_dep import SIDE\n' + plugin.read_text())
            original = lab.run_cached
            calls = []
            def change_after_first(args, *a, **k):
                result = original(args, *a, **k)
                calls.append(result)
                helper.write_text('SIDE=None\n')
                return result
            with mock.patch.object(lab, 'run_cached', side_effect=change_after_first):
                with self.assertRaisesRegex(ValueError, 'strategy changed after full-catalog'):
                    fixture.run_batch()
            self.assertEqual(len(calls), 1)
            self.assertFalse(list((fixture.root / 'batch').glob('*/completion.json')))
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
