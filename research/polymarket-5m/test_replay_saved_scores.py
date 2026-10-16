import gzip,json
import unittest
from contextlib import contextmanager
from tempfile import TemporaryDirectory

@contextmanager
def raises(exc,match):
 try:yield
 except exc as e:
  assert match in str(e),str(e)
 else:raise AssertionError("expected exception")
import replay_saved_scores as rs


def make(tmp_path):
 p=tmp_path/'endpoint.jsonl.gz'
 rows=[{'type':'dataset','dataset_id':'x','split':'development','recv_ms':86400000},
 {'type':'decision','market_id':'m','recv_ms':86401000,'features':{'tau_s':100},'feature_available_ms':{'tau_s':86401000}},
 {'type':'decision','market_id':'m','recv_ms':86402000,'features':{'tau_s':99},'feature_available_ms':{'tau_s':86402000}}]
 with gzip.open(p,'wt') as f:
  for r in rows:f.write(json.dumps(r)+'\n')
 s=tmp_path/'scores.jsonl.gz'
 with gzip.open(s,'wt') as f:f.write(json.dumps({'market_id':'m','recv_ms':86401000,'scores':{'book_5':.6,'r5':.3}})+'\n')
 reg=tmp_path/'registration.json';reg.write_text(json.dumps({'input_sha256':{'p':rs.sha(p)},'candidates':[{'id':'SYNTHETIC','model':'book_5'}]}))
 aud=tmp_path/'audit.json';aud.write_text(json.dumps([{'day':1,'model':'book_5','latest_label_ms':86399000}]))
 return p,s,reg,aud


def test_retains_missing_decision(tmp_path):
 p,s,r,a=make(tmp_path);out=tmp_path/'out';v=rs.prepare([p],s,r,a,out)
 rows=list(rs.read(out/p.name));assert len(rows)==3
 assert rows[1]['features']['book_5']==.6
 assert 'book_5' not in rows[2]['features']
 assert v['inputs'][0]['counts']['decisions_without_score']==1


def test_tape_tamper_rejected(tmp_path):
 p,s,r,a=make(tmp_path)
 with gzip.open(p,'at') as f:f.write('{}\n')
 with raises(ValueError,match='original model'):rs.prepare([p],s,r,a,tmp_path/'out')


def test_future_label_rejected(tmp_path):
 p,s,r,a=make(tmp_path);a.write_text(json.dumps([{'day':1,'model':'book_5','latest_label_ms':86400000}]))
 with raises(ValueError,match='earlier UTC day'):rs.prepare([p],s,r,a,tmp_path/'out')


def test_duplicate_score_rejected(tmp_path):
 p,s,r,a=make(tmp_path);row=list(rs.read(s))[0]
 with gzip.open(s,'at') as f:f.write(json.dumps(row)+'\n')
 with raises(ValueError,match='duplicate'):rs.prepare([p],s,r,a,tmp_path/'out')

class SavedScoreAdmissionTests(unittest.TestCase):
 def setUp(self):
  from pathlib import Path
  self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def test_retains_missing(self):test_retains_missing_decision(self.root)
 def test_tape_tamper(self):test_tape_tamper_rejected(self.root)
 def test_future_label(self):test_future_label_rejected(self.root)
 def test_duplicate_score(self):test_duplicate_score_rejected(self.root)

if __name__ == "__main__":unittest.main()
