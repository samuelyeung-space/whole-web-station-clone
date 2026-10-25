"""Synthetic transport failures and roundtrip; no financial replay."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import chunk_manifest_adapter as adapter


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = b'opaque original zip bytes' * 21
        self.chunks = []
        for i, start in enumerate(range(0, len(self.raw), 100)):
            payload = self.raw[start:start+100]
            path = self.root / f'saved-{i}.zip'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
                z.writestr('chunk.bin', payload)
            self.chunks.append({'index': i, 'file_name': path.name,
                                'local_path': '/untrusted/ignored/path',
                                'payload_bytes': len(payload),
                                'payload_sha256': hashlib.sha256(payload).hexdigest(),
                                'zip_bytes': path.stat().st_size,
                                'zip_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.manifest = self.root / 'recovery.json'
        self.data = {'schema': 'opaque-original-zip-subchunks-v1', 'originals': [{
            'original_file': 'original.zip', 'original_bytes': len(self.raw),
            'original_zip_sha256': hashlib.sha256(self.raw).hexdigest(),
            'chunks': self.chunks}]}
        self.out = self.root / 'out.zip'
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.data))
        self.sha = hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def restore(self):
        return adapter.restore(self.manifest, self.root, 'original.zip', self.out, self.sha)

    def blocked(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.restore()
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob('.chunk-recovery-*')), [])

    def test_roundtrip_ignores_local_path(self):
        result = self.restore()
        self.assertEqual(self.out.read_bytes(), self.raw)
        self.assertFalse(result['economic_replay_performed'])

    def test_missing_chunk(self):
        (self.root / self.chunks[1]['file_name']).unlink()
        self.blocked()

    def test_wrong_order(self):
        self.chunks.reverse(); self.save(); self.blocked()

    def test_symlink(self):
        p = self.root / self.chunks[0]['file_name']
        b = self.root / 'other.zip'; p.rename(b); p.symlink_to(b)
        self.blocked()

    def test_untrusted_manifest_change(self):
        self.manifest.write_text(self.manifest.read_text() + ' ')
        self.blocked()

    def test_payload_hash_wrong_despite_valid_outer_zip(self):
        self.chunks[0]['payload_sha256'] = '0' * 64
        self.save(); self.blocked()

    def test_traversal(self):
        self.chunks[0]['file_name'] = '../saved-0.zip'
        self.save(); self.blocked()

    def test_no_overwrite(self):
        self.out.write_bytes(b'keep')
        with self.assertRaises(ValueError): self.restore()
        self.assertEqual(self.out.read_bytes(), b'keep')


if __name__ == '__main__': unittest.main()
