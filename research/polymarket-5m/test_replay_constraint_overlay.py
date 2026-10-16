import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

import unittest
import tempfile
import replay_constraint_overlay as overlay
import replay_dataset as dataset


def fixture(tmp_path):
    mid='m'; markets=[{'conditionId':mid,'_recorded_at_ms':0,'orderMinSize':5,'orderPriceMinTickSize':0.5}]
    clob=[{'kind':'clob_snapshot','market_id':mid,'asset_id':'u','recv_ms':10,'source_ts_ms':9,'seq':1,'tick_size':0.001},
          {'kind':'clob_snapshot','market_id':mid,'asset_id':'d','recv_ms':10,'source_ts_ms':9,'seq':2,'tick_size':0.01},
          {'kind':'clob_error','recv_ms':20,'seq':3},
          {'kind':'clob_snapshot','market_id':mid,'asset_id':'u','recv_ms':30,'source_ts_ms':29,'seq':4,'tick_size':0.01}]
    native=b'{"run_id":"1"}\n'
    gamma=gzip.compress(b''.join(overlay.encode(r) for r in markets),mtime=0)
    members={'bundle-btc/strict/manifest.json':native,'bundle-btc/strict/clob_events.jsonl.gz':gzip.compress(b''.join(overlay.encode(r) for r in clob),mtime=0),'raw/2026-10-01/markets.jsonl.gz':gamma}
    tgz=io.BytesIO()
    with tarfile.open(fileobj=tgz,mode='w:gz') as tar:
        for name,data in members.items():
            m=tarfile.TarInfo(name);m.size=len(data);tar.addfile(m,io.BytesIO(data))
    archive=tmp_path/'archive.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('raw.tgz',tgz.getvalue())
    header={'type':'dataset','recv_ms':0,'dataset_id':'historical-spot-development:1','split':'development','observed_intervals_ms':[[0,300000]],'provenance':{'source':'synthetic_acceptance',
      'parent_manifest_sha256':hashlib.sha256(native).hexdigest(),'raw_metadata_sha256':{'raw/2026-10-01/markets.jsonl.gz':hashlib.sha256(gamma).hexdigest()}}}
    rows=[header,{'type':'market','market_id':mid,'recv_ms':0,'start_ms':0,'end_ms':300000,'up_token_id':'u','down_token_id':'d','fee_rate':0.07,'label_source':'gamma'},
          {'type':'constraint','market_id':mid,'recv_ms':0,'min_order_size':5,'up_tick_size':0.5,'down_tick_size':0.5,'constraints_source':'gamma'},
          {'type':'book','market_id':mid,'recv_ms':15,'up_asks':[[0.51,10]],'down_asks':[[0.5,10]]},
          {'type':'decision','market_id':mid,'recv_ms':25,'features':{'x':1}},
          {'type':'decision','market_id':mid,'recv_ms':35,'features':{'x':2}},
          {'type':'outcome','market_id':mid,'recv_ms':300001,'resolved_ms':300000,'winner':'Up','source':'gamma'}]
    tape=tmp_path/'normalized.jsonl.gz'
    with gzip.open(tape,'wb') as f:
        for r in rows:f.write(json.dumps(r,indent=None).encode()+b'\n')
    (tmp_path/'manifest.json').write_text(json.dumps({'normalized_sha256':overlay.sha(tape),'identity':{'inputs':{'archive_sha256':overlay.sha(archive)}}}))
    return tape,archive,rows


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_direct_ticks_disconnect_null_and_byte_preservation(self):
        tmp_path=self.root
        tape,archive,rows=fixture(tmp_path);oldhash=overlay.sha(tape)
        result=overlay.correct(tape,archive,tmp_path/'out')
        assert overlay.sha(tape)==oldhash
        parsed=list(overlay.extractor.read_jsonl(result['normalized_path']))
        constraints=[r for r in parsed if r['type']=='constraint']
        assert next(r for r in constraints if r['recv_ms']==10)['up_tick_size']==0.001
        assert next(r for r in constraints if r['recv_ms']==10)['down_tick_size']==0.01
        invalid=next(r for r in constraints if r['recv_ms']==20)
        assert invalid['up_tick_size'] is None and invalid['down_tick_size'] is None
        restored=next(r for r in constraints if r['recv_ms']==30)
        assert restored['up_tick_size']==0.01 and 'down_tick_size' not in restored
        assert all(r.get('up_tick_size')!=0.5 for r in constraints)
        assert result['copy_verification']['retained_mixed_constraint_events']==0
        dataset._scan(Path(result['normalized_path']))
        assert overlay.inspect(tape)['nonconstraint_sha256']==overlay.inspect(result['normalized_path'])['nonconstraint_sha256']
        assert result['formal_oos_result'] is None
    
    
    def test_idempotence_then_corruption_refused(self):
        tmp_path=self.root
        tape,archive,_=fixture(tmp_path);out=tmp_path/'out'
        first=overlay.correct(tape,archive,out);second=overlay.correct(tape,archive,out)
        assert second['cache_hit'] and first['normalized_path']==second['normalized_path']
        Path(first['normalized_path']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'integrity mismatch'):overlay.correct(tape,archive,out)
    
    
    def test_wrong_archive_receipt_provenance_refused(self):
        tmp_path=self.root
        tape,archive,rows=fixture(tmp_path)
        rows[0]['provenance']['raw_metadata_sha256']['raw/2026-10-01/markets.jsonl.gz']='wrong'
        with gzip.open(tape,'wb') as f:f.write(b''.join(overlay.encode(r) for r in rows))
        (tmp_path/'manifest.json').write_text(json.dumps({'normalized_sha256':overlay.sha(tape),'identity':{'inputs':{'archive_sha256':overlay.sha(archive)}}}))
        with self.assertRaisesRegex(ValueError,'Gamma receipts'):overlay.correct(tape,archive,tmp_path/'out')
        assert not list((tmp_path/'out').glob('*/manifest.json'))
    
    
    def test_truncated_input_refused_before_publication(self):
        tmp_path=self.root
        tape,archive,_=fixture(tmp_path);tape.write_bytes(tape.read_bytes()[:-8])
        with self.assertRaises(EOFError):overlay.correct(tape,archive,tmp_path/'out')
        assert not (tmp_path/'out').exists()
    
    
    def test_mixed_constraint_semantics_rejected(self):
        tape,archive,rows=fixture(self.root)
        rows[2]['fee_rate']=0.06
        with gzip.open(tape,'wb') as f:f.write(b''.join(overlay.encode(r) for r in rows))
        with self.assertRaisesRegex(ValueError,'unsupported mixed constraint'):
            overlay.correct(tape,archive,self.root/'out')

    def test_native_archive_binds_to_completed_producer(self):
        tape,archive,_=fixture(self.root)
        with zipfile.ZipFile(archive,'a') as z:z.writestr('extra.txt','changed archive')
        with self.assertRaisesRegex(ValueError,'archive differs'):
            overlay.correct(tape,archive,self.root/'out')

    def test_resumed_native_clob_binds_to_completed_audit(self):
        tape,archive,_=fixture(self.root)
        (self.root/'manifest.json').write_text(json.dumps({
            'normalization':{'sha256':overlay.sha(tape)},
            'identity':{'audit_inputs':{'clob_events.jsonl.gz':'wrong'}}}))
        with self.assertRaisesRegex(ValueError,'native CLOB differs'):
            overlay.correct(tape,archive,self.root/'out')
        assert not list((self.root/'out').glob('*/manifest.json'))

    def test_source_regression_never_restores_stale_tick(self):
        markets={'m':{'market_id':'m','recv_ms':0,'up_token_id':'u','down_token_id':'d'}}
        clob=[{'kind':'clob_price_change','market_id':'m','asset_id':'u','recv_ms':1,'source_ts_ms':10,'seq':1},
              {'kind':'clob_snapshot','market_id':'m','asset_id':'u','recv_ms':2,'source_ts_ms':9,'seq':2,'tick_size':0.01}]
        constraints,audit=overlay.extractor.extract_constraints(markets,[],clob)
        assert not constraints and audit['counts']['regressed_source_rows_ignored']==1

if __name__=="__main__": unittest.main()
