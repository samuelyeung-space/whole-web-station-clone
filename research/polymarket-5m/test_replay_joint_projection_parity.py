"""Adversarial engineering fixtures; no real market backtests or strategy PnL."""
import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest

import replay_joint_projection_parity as parity


class ProjectionParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.left = [{'type': 'dataset', 'dataset_id': 'jpr'},
                     {'type': 'book', 'recv_ms': 10, 'market_id': 'm', 'asks': [[.5, 10.0]]},
                     {'type': 'decision', 'recv_ms': 10, 'market_id': 'm',
                      'features': {'book_5': .7, 'tau_s': 30},
                      'feature_available_ms': {'book_5': 9, 'tau_s': 10}}]
        self.right = copy.deepcopy(self.left)
        self.right[0]['dataset_id'] = 'mar'
        self.right[2]['features']['extra_mar_z'] = 100

    def tearDown(self):
        self.temp.cleanup()

    def check(self):
        for name, rows in [('left.gz', self.left), ('right.gz', self.right)]:
            with gzip.open(self.root / name, 'wt') as f:
                for row in rows:
                    f.write(json.dumps(row) + '\n')
        (self.root / 'manifest.json').write_text(json.dumps({'inputs': [
            {'output_path': 'right.gz', 'output_sha256': parity.sha(self.root / 'right.gz')}] }))
        return parity.verify(['left.gz'], 'manifest.json', self.root / 'audit.json', input_root=self.root)

    def test_extra_features_and_adapter_identity_allowed_from_unrelated_cwd(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as cwd:
            try:
                os.chdir(cwd)
                result = self.check()
            finally:
                os.chdir(previous)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['tapes'][0]['event_counts']['decision'], 1)
        self.assertEqual(result['new_backtests'], 0)

    def test_required_feature_change_rejected(self):
        self.right[2]['features']['book_5'] = .8
        with self.assertRaisesRegex(ValueError, 'projection differs'):
            self.check()

    def test_future_availability_change_rejected(self):
        self.right[2]['feature_available_ms']['book_5'] = 11
        with self.assertRaisesRegex(ValueError, 'projection differs'):
            self.check()

    def test_changed_execution_book_rejected(self):
        self.right[1]['asks'][0][1] = 20.0
        with self.assertRaisesRegex(ValueError, 'projection differs'):
            self.check()

    def test_python_boolean_numeric_equality_not_admitted(self):
        self.left[2]['features']['book_5'] = 1
        self.right[2]['features']['book_5'] = True
        with self.assertRaisesRegex(ValueError, 'projection differs'):
            self.check()

    def test_missing_event_rejected(self):
        self.right.pop()
        with self.assertRaisesRegex(ValueError, 'Event count differs'):
            self.check()

    def test_event_reordering_rejected(self):
        self.right[1], self.right[2] = self.right[2], self.right[1]
        with self.assertRaisesRegex(ValueError, 'projection differs'):
            self.check()


if __name__ == '__main__':
    unittest.main()
