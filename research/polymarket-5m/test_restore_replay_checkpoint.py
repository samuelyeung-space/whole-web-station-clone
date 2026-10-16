import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from restore_replay_checkpoint import restore, sha


class RestoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.json'
        self.out = self.root / 'cache'

    def make(self, name='baseline/input.json', bad_member=False, extra=False):
        data = b'{"split":"development"}\n'
        manifest = {'members': {name: {'bytes': len(data), 'sha256': '0'*64 if bad_member else sha(data)}}}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr(name, data)
            z.writestr('CHECKPOINT_MANIFEST.json', json.dumps(manifest))
            if extra:
                z.writestr('surprise.py', 'raise RuntimeError()')
        payload = buf.getvalue()
        c = {'encoding': 'zip+base64', 'bytes': len(payload), 'sha256': sha(payload),
             'manifest': manifest, 'git_head': 'test', 'data_base64': base64.b64encode(payload).decode()}
        self.state.write_text(json.dumps({'embedded_replay_checkpoints': {'test': c}}))

    def test_restore_and_reuse(self):
        self.make()
        self.assertFalse(restore(self.state, 'test', self.out)['reused'])
        self.assertTrue(restore(self.state, 'test', self.out)['reused'])

    def test_corrupt_existing_never_overwrites(self):
        self.make(); restore(self.state, 'test', self.out)
        p = self.out / 'baseline/input.json'; p.write_text('corrupt')
        with self.assertRaises(ValueError): restore(self.state, 'test', self.out)
        self.assertEqual(p.read_text(), 'corrupt')

    def test_bad_member_hash(self):
        self.make(bad_member=True)
        with self.assertRaises(ValueError): restore(self.state, 'test', self.out)
        self.assertFalse(self.out.exists())

    def test_traversal(self):
        self.make(name='../outside')
        with self.assertRaises(ValueError): restore(self.state, 'test', self.out)
        self.assertFalse((self.root / 'outside').exists())

    def test_unexpected_member(self):
        self.make(extra=True)
        with self.assertRaises(ValueError): restore(self.state, 'test', self.out)

    def test_outer_hash(self):
        self.make(); s = json.loads(self.state.read_text())
        s['embedded_replay_checkpoints']['test']['sha256'] = '0'*64
        self.state.write_text(json.dumps(s))
        with self.assertRaises(ValueError): restore(self.state, 'test', self.out)


if __name__ == '__main__':
    unittest.main()
