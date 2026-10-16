"""Failure recovery tests use synthetic bytes, never profitability fixtures."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import strict_history_resume as recovery

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stage = self.root/'stage'
        strict = self.stage/'extracted/bundle-btc/strict'
        strict.mkdir(parents=True)
        (strict/'manifest.json').write_text('{}')
        self.source = strict/'source_events.jsonl.gz'
        self.source.write_bytes(gzip.compress(b''))
        evidence = {'input_sha256': {p.name: recovery.sha(p) for p in strict.iterdir()},
            'audit_code_sha256': recovery.sha(recovery.audit.__file__),
            'validator_sha256': recovery.sha(recovery.contracts.__file__)}
        (self.stage/'stream_audit.json').write_text(json.dumps(evidence))
        spot = self.stage/'spot'; spot.mkdir()
        (spot/'source_events.jsonl.gz').write_bytes(gzip.compress(b''))
        (spot/'manifest.json').write_text(json.dumps({'code_sha256':recovery.sha(recovery.projection.__file__),
            'output_sha256': recovery.sha(spot/'source_events.jsonl.gz')}))
        self.out = self.root/'out'
        self.header = {'type':'dataset','dataset_id':'synthetic-recovery','split':'development'}
    def tearDown(self): self.temp.cleanup()
    def producer(self, *args, **kwargs):
        self.assertTrue(kwargs['allow_unresolved'])
        output = args[3]
        output.write_bytes(gzip.compress((json.dumps(self.header)+'\n'+json.dumps({'type':'book'})+'\n').encode()))
        return {'sha256':recovery.sha(output),'header':self.header,'counts':{'book':1}}
    def test_interrupted_requires_explicit_acknowledgement(self):
        with self.assertRaisesRegex(ValueError,'explicit interrupted'):
            recovery.resume(self.stage,self.out)
    def test_interrupted_producer_completes_then_hash_checked_cache_reused(self):
        with patch.object(recovery.normalizer,'normalize',side_effect=self.producer) as mocked:
            a=recovery.resume(self.stage,self.out,interrupted=True)
            b=recovery.resume(self.stage,self.out,interrupted=True)
            self.assertEqual(mocked.call_count,1); self.assertTrue(b['cache_hit'])
            self.assertTrue(a['integrity']['gzip_eof_crc_verified'])
            Path(a['normalized_path']).write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'corrupted'):
                recovery.resume(self.stage,self.out,interrupted=True)
    def test_mutated_audited_input_refused_before_production(self):
        self.source.write_bytes(b'changed')
        with patch.object(recovery.normalizer,'normalize') as mocked:
            with self.assertRaisesRegex(ValueError,'audit input changed'):
                recovery.resume(self.stage,self.out,interrupted=True)
            mocked.assert_not_called()
    def test_failed_output_and_second_attempt_are_preserved(self):
        (self.stage/'failed.json').write_text('{"error":"previous producer failed"}')
        def broken(*a,**kw):
            result=self.producer(*a,**kw)
            a[3].write_bytes(a[3].read_bytes()[:-8])
            result['sha256']=recovery.sha(a[3]); return result
        with patch.object(recovery.normalizer,'normalize',side_effect=broken):
            with self.assertRaisesRegex(ValueError,'integrity failure'):
                recovery.resume(self.stage,self.out)
        with patch.object(recovery.normalizer,'normalize',side_effect=self.producer):
            result=recovery.resume(self.stage,self.out)
        self.assertTrue(result['integrity']['gzip_eof_crc_verified'])
        self.assertEqual(len(list(self.out.glob('*.interrupted.*/failed.json'))),1)
        self.assertEqual(json.loads((self.stage/'failed.json').read_text())['error'],'previous producer failed')

if __name__=='__main__': unittest.main()
