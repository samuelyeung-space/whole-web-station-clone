"""File-boundary synthetic tests; no historical PnL claims."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_usdm_flow_execution_bridge import fixture
import usdm_flow_bundle_replay as b

class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        events,bindings=fixture(); tape=bindings[0].pop('tape')
        events[0]['capture_clock_id']='synthetic:recv_ms'
        contents={'events':''.join(json.dumps(x)+'\n' for x in events).encode(),
          'trades':gzip.compress(''.join(json.dumps(x['raw_recorder_row'])+'\n' for x in tape.retained).encode()),
          'coverage':json.dumps([vars(x) for x in tape.engine.coverage]).encode(),
          'bindings':json.dumps(bindings).encode()}
        self.m=dict(schema=b.VERSION,split='development',input_evidence_class='synthetic_fixture',
            consumption_provenance='synthetic only, not real coverage',capture_run_id='synthetic',
            capture_clock_id='synthetic:recv_ms',recorder_contract=b.CONTRACT,time_unit='MILLISECOND',trades_encoding='gzip-jsonl',files={})
        for role,data in contents.items(): self.write(role,data)
    def write(self,role,data):
        (self.root/role).write_bytes(data)
        self.m['files'][role]=dict(path=role,bytes=len(data),sha256=b.digest(data))
    def run_bundle(self):
        p=self.root/'manifest.json';p.write_text(json.dumps(self.m));return b.replay_bundle(p,expected_manifest_sha256=b.digest(p.read_bytes()))
    def test_file_snapshots_reach_existing_engine(self):
        r=self.run_bundle();self.assertEqual(r['bundle_audit']['native_trade_rows'],2)
        self.assertEqual(len(r['bundle_audit']['input_identities']),4)
        self.assertFalse(r['bundle_audit']['execution_sealed']);self.assertFalse(r['main_ranking_admitted'])
        self.assertTrue(r['scenarios']['base']['attempts'])
    def test_mismatch_aborts_before_native_parse_or_replay(self):
        (self.root/'trades').write_bytes(b'corrupt gzip')
        with patch.object(b,'rows',side_effect=AssertionError('must not parse')),self.assertRaisesRegex(ValueError,'mismatch'): self.run_bundle()
    def test_clock_and_coverage_run_mismatch_rejected(self):
        self.m['capture_clock_id']='wrong'
        with self.assertRaisesRegex(ValueError,'clock/run'):self.run_bundle()
        self.m['capture_clock_id']='synthetic:recv_ms'
        c=json.loads((self.root/'coverage').read_bytes());c[0]['capture_run_id']='other';self.write('coverage',json.dumps(c).encode())
        with self.assertRaisesRegex(ValueError,'coverage venue'):self.run_bundle()
    def test_no_coverage_keeps_no_attempts(self):
        self.write('coverage',b'[]');r=self.run_bundle()
        self.assertTrue(all(not x['attempts'] for x in r['scenarios'].values()))
    def test_corrupt_trade_is_not_repaired(self):
        raw=[json.loads(x) for x in gzip.decompress((self.root/'trades').read_bytes()).splitlines()];raw[0]['q']='0'
        self.write('trades',gzip.compress(('\n'.join(json.dumps(x) for x in raw)+'\n').encode()))
        with patch.object(b.bridge,'replay_development',side_effect=AssertionError('must not replay')),self.assertRaises(ValueError):self.run_bundle()
    def test_manifest_pin_split_duplicate_keys_and_path_escape(self):
        p=self.root/'manifest.json';p.write_text('{}')
        with self.assertRaisesRegex(ValueError,'manifest SHA'):b.replay_bundle(p,expected_manifest_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'duplicate JSON'):b.loads('{"split":"development","split":"OOS"}')
        self.m['split']='OOS'
        with self.assertRaisesRegex(ValueError,'development'):self.run_bundle()
        self.m['split']='development';self.m['files']['events']['path']='../outside'
        with self.assertRaisesRegex(ValueError,'bundle-relative'):self.run_bundle()
    def test_cli_rejects_without_output(self):
        p=self.root/'manifest.json';p.write_text(json.dumps(self.m));out=self.root/'out.json'
        x=subprocess.run([sys.executable,b.__file__,str(p),'--sha256','0'*64,'--output',str(out)],capture_output=True)
        self.assertNotEqual(x.returncode,0);self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
