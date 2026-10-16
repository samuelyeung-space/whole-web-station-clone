"""Dependency/cache identity counterexamples; synthetic rules, not backtests."""
import importlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import sys
import tempfile
import unittest

from replay_plugin_identity import PluginSnapshot, load_plugin


class PluginIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sys.path.insert(0, str(self.root))

    def tearDown(self):
        sys.path.remove(str(self.root))
        for name in list(sys.modules):
            if name.startswith('closure_fixture'):
                sys.modules.pop(name)
        self.tmp.cleanup()

    def write(self, path, text):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        importlib.invalidate_caches()
        return path

    def base(self):
        self.write('closure_fixture.py', 'from closure_fixture_helper import score\n'
                   'def signal(c, f):\n    return score(f)\n')
        return self.write('closure_fixture_helper.py', 'def score(f):\n    return 1\n')

    def test_transitive_helper_change_invalidates_identity_and_executes_new_bytes(self):
        self.base()
        self.write('closure_fixture_helper.py', 'from closure_fixture_leaf import score\n')
        self.write('closure_fixture_leaf.py', 'def score(f):\n    return 1\n')
        one, first = load_plugin('closure_fixture')
        self.write('closure_fixture_leaf.py', 'def score(f):\n    return 2\n')
        two, second = load_plugin('closure_fixture')
        self.assertEqual(one.signal({}, {}), 1)
        self.assertEqual(two.signal({}, {}), 2)
        self.assertNotEqual(first.identity_sha256, second.identity_sha256)
        self.assertEqual(len(second.sources), 3)
        with self.assertRaisesRegex(ValueError, 'dependency changed'):
            first.verify()

    def test_same_mtime_size_pyc_and_existing_modules_cannot_supply_stale_rule(self):
        path = self.base()
        stale = importlib.import_module('closure_fixture_helper')
        py_compile.compile(str(path), doraise=True)
        timestamp = path.stat().st_mtime_ns
        self.assertEqual(stale.score({}), 1)
        path.write_text('def score(f):\n    return 2\n')
        os.utime(path, ns=(timestamp, timestamp))
        module, snapshot = load_plugin('closure_fixture')
        self.assertEqual(module.signal({}, {}), 2)
        self.assertIs(sys.modules['closure_fixture_helper'], stale)
        snapshot.verify()

    def test_function_scope_import_remains_pinned_across_later_plugin_loads(self):
        self.write('closure_fixture.py', 'def signal(c, f):\n'
                   '    from closure_fixture_helper import score\n    return score(f)\n')
        self.write('closure_fixture_helper.py', 'def score(f):\n    return 1\n')
        old, first = load_plugin('closure_fixture')
        self.write('closure_fixture_helper.py', 'def score(f):\n    return 2\n')
        new, second = load_plugin('closure_fixture')
        self.assertEqual(old.signal({}, {}), 1)
        self.assertEqual(new.signal({}, {}), 2)
        with self.assertRaisesRegex(ValueError, 'dependency changed'):
            first.verify()
        second.verify()

    def test_relative_package_imports_and_package_initializers_hashed(self):
        self.write('closure_fixture_pkg/__init__.py', 'OFFSET = 3\n')
        self.write('closure_fixture_pkg/helper.py', 'from . import OFFSET\n'
                   'def score(f):\n    return OFFSET\n')
        self.write('closure_fixture_pkg/plugin.py', 'from . import helper\n'
                   'def signal(c, f):\n    return helper.score(f)\n')
        module, snapshot = load_plugin('closure_fixture_pkg.plugin')
        self.assertEqual(module.signal({}, {}), 3)
        self.assertEqual(set(snapshot.sources), {'closure_fixture_pkg', 'closure_fixture_pkg.helper',
                                                'closure_fixture_pkg.plugin'})

    def test_package_submodule_absolute_import_and_dataclass(self):
        self.write('closure_fixture_pkg/__init__.py', '')
        self.write('closure_fixture_pkg/helper.py', 'from dataclasses import dataclass\n'
                   '@dataclass\nclass Value:\n    x: int = 9\n')
        self.write('closure_fixture.py', 'import closure_fixture_pkg.helper\n'
                   'def signal(c, f):\n    return closure_fixture_pkg.helper.Value().x\n')
        module, snapshot = load_plugin('closure_fixture')
        self.assertEqual(module.signal({}, {}), 9)
        self.assertNotIn('dataclasses', snapshot.sources)

    def test_move_same_sources_does_not_change_identity(self):
        self.base()
        _, first = load_plugin('closure_fixture')
        relocated = self.root / 'relocated'
        relocated.mkdir()
        for path in self.root.glob('*.py'):
            shutil.copyfile(path, relocated / path.name)
        sys.path.insert(0, str(relocated))
        try:
            _, second = load_plugin('closure_fixture')
        finally:
            sys.path.remove(str(relocated))
        self.assertNotEqual(first.source_paths, second.source_paths)
        self.assertEqual(first.identity, second.identity)

    def test_missing_declared_dependency_and_nonliteral_declaration_fail_closed(self):
        for body, message in [
            ("REPLAY_DEPENDENCIES = ['closure_fixture_absent']\n", 'missing declared/imported'),
            ("REPLAY_DEPENDENCIES = list()\n", 'literal list')]:
            self.write('closure_fixture.py', body)
            with self.assertRaisesRegex(ValueError, message):
                load_plugin('closure_fixture')

    def test_declared_dependency_and_static_import_inside_dead_branch_both_hash(self):
        self.write('closure_fixture.py', "REPLAY_DEPENDENCIES = ['closure_fixture_declared']\n"
                   'if False:\n    import closure_fixture_helper\n')
        self.write('closure_fixture_declared.py', 'VALUE=1\n')
        self.write('closure_fixture_helper.py', 'VALUE=2\n')
        _, snapshot = load_plugin('closure_fixture')
        self.assertEqual(len(snapshot.sources), 3)

    def test_dynamic_import_and_code_generation_fail_closed(self):
        cases = ['import importlib as loader\n', 'from importlib import import_module\n',
                 'x = __import__("math")\n', 'x = eval("1")\n',
                 'import sys\nx=sys.modules\n', 'from builtins import __import__\n']
        for body in cases:
            with self.subTest(body=body):
                self.write('closure_fixture.py', body)
                with self.assertRaisesRegex(ValueError, 'unsupported'):
                    load_plugin('closure_fixture')

    def test_local_stdlib_name_shadow_is_hashed(self):
        self.write('closure_fixture.py', 'from fractions import SCORE\n')
        self.write('fractions.py', 'SCORE=7\n')
        module, snapshot = load_plugin('closure_fixture')
        self.assertEqual(module.SCORE, 7)
        self.assertIn('fractions', snapshot.sources)

    def test_real_mine500_catalog_equivalence_and_python_identity(self):
        import mine500_catalog as baseline
        module, snapshot = load_plugin('mine500_catalog')
        self.assertEqual(module.make_candidates(), baseline.make_candidates())
        self.assertEqual(module.make_supplementary_candidates(), baseline.make_supplementary_candidates())
        self.assertEqual(set(snapshot.sources), {'mine500_catalog'})
        self.assertEqual(snapshot.identity['python']['version'], sys.version)
        json.dumps(snapshot.identity, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
