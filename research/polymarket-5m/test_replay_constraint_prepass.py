import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

import replay_constraint_overlay as base
import replay_constraint_prepass as fast
from test_replay_constraint_overlay import fixture


class PrepassTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tape, self.archive, rows = fixture(self.root)
        parent = self.root / 'sources'
        files = base.unpack_constraint_sources(self.archive, parent)
        gamma_path = parent / 'raw/2026-10-01/markets.jsonl.gz'
        gamma = list(base.extractor.read_jsonl(gamma_path))
        gamma[0].update(slug='btc-updown-5m-0', outcomes=['Down', 'Up'], clobTokenIds=['d', 'u'])
        gamma_path.write_bytes(gzip.compress(b''.join(base.encode(row) for row in gamma), mtime=0))
        registry = parent / 'bundle-btc/strict/market_registry.csv.gz'
        registry.write_bytes(gzip.compress(b'market_id,start_ts,up_token_id,down_token_id\nm,0,u,d\n', mtime=0))
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as tar:
            for path in sorted(parent.rglob('*')):
                if path.is_file():
                    content = path.read_bytes()
                    info = tarfile.TarInfo(path.relative_to(parent).as_posix())
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
        with zipfile.ZipFile(self.archive, 'w') as target:
            target.writestr('raw.tgz', data.getvalue())
        rows[0]['provenance']['raw_metadata_sha256']['raw/2026-10-01/markets.jsonl.gz'] = base.sha(gamma_path)
        self.tape.write_bytes(gzip.compress(b''.join(base.encode(row) for row in rows), mtime=0))
        (self.root / 'manifest.json').write_text(json.dumps({'normalized_sha256': base.sha(self.tape),
            'identity': {'inputs': {'archive_sha256': base.sha(self.archive)}}}))
        self.prepass = fast.precompute(parent, self.archive, self.root / 'prepass')

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_constraint_and_nonconstraint_equivalence(self):
        original = base.correct(self.tape, self.archive, self.root / 'original')
        result = fast.correct(self.tape, self.archive, self.prepass['prepass_path'], self.root / 'fast')
        self.assertEqual(original['constraint_events_sha256'], result['constraint_events_sha256'])
        self.assertEqual(original['constraint_extraction_audit'], result['constraint_extraction_audit'])
        self.assertEqual(original['normalization']['counts'], result['normalization']['counts'])
        self.assertEqual(original['copy_verification'], result['copy_verification'])
        self.assertTrue(fast.correct(self.tape, self.archive, self.prepass['prepass_path'], self.root / 'fast')['cache_hit'])

    def test_market_map_drift_rejected(self):
        path = Path(self.prepass['prepass_path']) / 'manifest.json'
        record = json.loads(path.read_text())
        record['market_maps']['m']['recv_ms'] = 1
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'market maps differ'):
            fast.correct(self.tape, self.archive, path.parent, self.root / 'fast')

    def test_constraint_corruption_rejected(self):
        parent = Path(self.prepass['prepass_path'])
        (parent / 'constraints.jsonl').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'constraints corrupted'):
            fast.correct(self.tape, self.archive, parent, self.root / 'fast')

    def test_original_archive_members_reverified(self):
        path = Path(self.prepass['prepass_path']) / 'manifest.json'
        record = json.loads(path.read_text())
        record['identity']['source_members_sha256']['bundle-btc/strict/clob_events.jsonl.gz'] = 'different'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'native sources differ'):
            fast.correct(self.tape, self.archive, path.parent, self.root / 'fast')


if __name__ == '__main__':
    unittest.main()
