import tempfile,pathlib,json,hashlib,gzip,sys,unittest
from immutable_input_guard import run,preflight,InputRejected
class GuardTests(unittest.TestCase):
 def fixture(self,root,data=b'fixed',name='input.bin'):
  p=pathlib.Path(root)/name;p.write_bytes(data)
  return p,[dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())]
 def test_stable_input_passes_without_profit_claim(self):
  with tempfile.TemporaryDirectory() as d:
   p,m=self.fixture(d);r=run(d,m,[sys.executable,'-c','pass'],.001)
   self.assertTrue(r['input_integrity_passed']);self.assertFalse(r['financial_profitability_validated'])
 def test_wrong_hash_never_starts_worker(self):
  with tempfile.TemporaryDirectory() as d:
   p,m=self.fixture(d);m[0]['sha256']='0'*64;mark=pathlib.Path(d)/'started'
   with self.assertRaises(InputRejected):run(d,m,[sys.executable,'-c',f"open({str(mark)!r},'w').close()"])
   self.assertFalse(mark.exists())
 def test_mutating_worker_rejected_even_if_exits_zero(self):
  with tempfile.TemporaryDirectory() as d:
   p,m=self.fixture(d)
   with self.assertRaises(InputRejected):run(d,m,[sys.executable,'-c',f"open({str(p)!r},'wb').write(b'')"],.001)
 def test_matching_hash_of_truncated_gzip_still_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p,m=self.fixture(d,gzip.compress(b'fixed')[:-2],'input.gz')
   with self.assertRaises(InputRejected):preflight(d,m)
if __name__=='__main__':unittest.main()
