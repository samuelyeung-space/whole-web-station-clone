import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from replay_provenance_amendment import amend, sha


class ProvenanceAmendmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache, self.sources, self.out = [self.root/x for x in ('cache', 'source', 'out')]
        self.cache.mkdir(); self.sources.mkdir()
        self.blobs, codes = {}, {}
        for name in ('historical_spot_development.py', 'replay_raw.py'):
            data = ('# exact old producer '+name+'\n').encode()
            (self.sources/name).write_bytes(data)
            self.blobs[name] = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            codes[name] = hashlib.sha256(data).hexdigest()
        self.actual = codes['historical_spot_development.py']
        self.header = {'type': 'dataset', 'dataset_id': 'known', 'recv_ms': 1000,
                       'provenance': {'adapter_code_sha256': 'new-file-on-disk', 'preserve': 17}}
        # Deliberately noncanonical body spacing, Unicode, and exact float formatting.
        self.body = b'{ "type" : "book", "recv_ms": 1000.00, "memo":"\xe4\xb8\xad" }\n'+b'{"type":"decision","recv_ms":1001}\n'
        self.tape = self.cache/'normalized.jsonl.gz'
        with gzip.open(self.tape, 'wb') as f:
            f.write(json.dumps(self.header).encode()+b'\n'+self.body)
        self.manifest = {'identity': {'code_sha256': codes}, 'normalized_sha256': sha(self.tape),
                         'normalization': {'sha256': sha(self.tape), 'header': self.header,
                                           'counts': {'book': 1, 'decision': 1}}}
        self.write_manifest()

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self):
        (self.cache/'manifest.json').write_text(json.dumps(self.manifest))

    def run_amend(self):
        return amend(self.cache, self.out, self.sources, source_commit='a'*40,
                     git_blob_ids=self.blobs, reason='old module loaded before disk edit')

    def test_body_exact_and_source_immutable_with_idempotent_reuse(self):
        before = {p.name: p.read_bytes() for p in self.cache.iterdir()}
        r = self.run_amend()
        with gzip.open(r['normalized_path'], 'rb') as f:
            header = json.loads(f.readline()); body = f.read()
        self.assertEqual(body, self.body)
        self.assertEqual(header['provenance']['adapter_code_sha256'], self.actual)
        self.assertEqual(header['provenance']['preserve'], 17)
        self.assertEqual(header['provenance']['code_identity_amendment']['recorded_adapter_code_sha256'], 'new-file-on-disk')
        self.assertEqual(r['body_sha256'], hashlib.sha256(self.body).hexdigest())
        self.assertEqual(r['new_backtests'], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.cache.iterdir()})
        second = self.run_amend()
        self.assertTrue(second['cache_hit'])
        self.assertEqual(r['normalized_path'], second['normalized_path'])

    def test_truncated_gzip_rejected_even_when_manifest_hash_matches(self):
        self.tape.write_bytes(self.tape.read_bytes()[:-8])
        self.manifest['normalized_sha256'] = sha(self.tape)
        self.manifest['normalization']['sha256'] = sha(self.tape)
        self.write_manifest()
        with self.assertRaises((EOFError, OSError)):
            self.run_amend()
        self.assertFalse(self.out.exists())

    def test_source_identity_disagreement_refuses_guess(self):
        self.manifest['identity']['code_sha256']['replay_raw.py'] = 'f'*64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'startup producer identity differs'):
            self.run_amend()

    def test_independent_git_blob_verification_required(self):
        self.blobs['historical_spot_development.py'] = '0'*40
        with self.assertRaisesRegex(ValueError, 'independent Git source verification failed'):
            self.run_amend()

    def test_matching_header_creates_nothing(self):
        self.header['provenance']['adapter_code_sha256'] = self.actual
        with gzip.open(self.tape, 'wb') as f:
            f.write(json.dumps(self.header).encode()+b'\n'+self.body)
        self.manifest['normalized_sha256'] = sha(self.tape)
        self.manifest['normalization']['sha256'] = sha(self.tape)
        self.write_manifest()
        r = self.run_amend()
        self.assertEqual(r['status'], 'no_amendment_needed')
        self.assertFalse(self.out.exists())

    def test_incomplete_producer_not_admitted(self):
        pending = self.root/'cache.pending.example'
        self.cache.rename(pending); self.cache = pending
        with self.assertRaisesRegex(ValueError, 'not published'):
            self.run_amend()


if __name__ == '__main__':
    unittest.main()
