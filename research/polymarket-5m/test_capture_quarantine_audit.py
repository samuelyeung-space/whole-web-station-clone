import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from capture_quarantine_audit import audit


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.rows=[]
        self.runtime={'started_ms':1792195200000,'ended_ms':1792195200000+14400000,
                      'complete':True,'writer':{'accepted':4,'written':4,'flushed':4,
                                               'pending_bytes':0,'error':None}}
        self.write('capture-runtime.json',(json.dumps(self.runtime)+'\n').encode())
        self.stream('markets',[{'slug':'btc-updown-5m-1792195200'},
                               {'slug':'btc-updown-5m-1792195200'},
                               {'slug':'eth-updown-5m-1792195200'}])
        self.stream('binance',[{'recv_ms':1792195200100,'T':1792195200000,
                                'p':'1','q':'2','m':True}])
        self.stream('capture-health',[{'at':1792195200001,'clob_active':True,
                                       'event_loop_lag_ms':5}])
        self.stream('decisions',[{'secret_signal_value':123.456}])

    def write(self,name,data):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.rows.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})

    def stream(self,name,rows):
        raw=''.join(json.dumps(r)+'\n' for r in rows).encode()
        self.write('raw/2026-10-17/'+name+'.jsonl.gz',gzip.compress(raw))

    def manifest(self):
        (self.root/'capture-manifest.json').write_text(json.dumps(
            {'schema':'capture-checkpoint-v1','run_id':'1','source_commit':'x','files':self.rows}))

    def run_audit(self):
        self.manifest();return audit(self.root)

    def test_no_economic_access_or_admission(self):
        r=self.run_audit()
        self.assertEqual(r['new_financial_replays'],0);self.assertIsNone(r['formal_oos_result'])
        self.assertFalse(r['execution_input_admitted']);self.assertTrue(r['writer_drained'])
        self.assertEqual(r['btc5m_registered_markets'],1)
        self.assertNotIn('123.456',json.dumps(r))
        self.assertFalse(any(d['full_runtime_day'] for d in r['daily_runtime']))
        trade=next(x for x in r['gzip_streams'] if x['path'].endswith('binance.jsonl.gz'))
        self.assertEqual(trade['field_presence_counts']['maker_flag_boolean'],1)

    def test_member_hash_mismatch(self):
        self.rows[-1]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.run_audit()

    def test_truncated_gzip_despite_correct_container_hash(self):
        name='raw/2026-10-17/truncated.jsonl.gz'
        self.write(name,gzip.compress(b'{}\n')[:-5])
        with self.assertRaises((EOFError,OSError)):self.run_audit()

    def test_unterminated_line_rejected(self):
        self.write('raw/2026-10-17/quote.jsonl.gz',gzip.compress(b'{}'))
        with self.assertRaisesRegex(ValueError,'unterminated'):self.run_audit()

    def test_duplicate_inventory_rejected(self):
        self.rows.append(dict(self.rows[-1]))
        with self.assertRaisesRegex(ValueError,'duplicate'):self.run_audit()

    def test_missing_receipt_span_rejected(self):
        p=self.root/'capture-runtime.json';r=json.loads(p.read_text());r['ended_ms']=None
        b=(json.dumps(r)+'\n').encode();p.write_bytes(b)
        self.rows[0].update(bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
        with self.assertRaisesRegex(ValueError,'runtime'):self.run_audit()

    def test_non_drained_not_called_healthy(self):
        p=self.root/'capture-runtime.json';r=json.loads(p.read_text());r['writer']['pending_bytes']=1
        b=(json.dumps(r)+'\n').encode();p.write_bytes(b)
        self.rows[0].update(bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
        result=self.run_audit()
        self.assertFalse(result['writer_drained']);self.assertFalse(result['complete_feed_health_verified'])


if __name__=='__main__':unittest.main()
