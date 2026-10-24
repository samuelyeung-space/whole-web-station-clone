import gzip,hashlib,io,tarfile,tempfile,unittest
from pathlib import Path
from verified_archive_member import load
h=lambda x:hashlib.sha256(x).hexdigest()
class Tests(unittest.TestCase):
 def fixture(self,payload=None,duplicate=False):
  d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);p=Path(d.name)/'a.tar'
  b=gzip.compress(b'abc\n'*100) if payload is None else payload
  with tarfile.open(p,'w') as t:
   for _ in range(2 if duplicate else 1):
    m=tarfile.TarInfo('strict/clob.gz');m.size=len(b);t.addfile(m,io.BytesIO(b))
  return p,dict(archive_sha256=h(p.read_bytes()),member='strict/clob.gz',member_bytes=len(b),member_sha256=h(b),max_compressed_bytes=1000,max_decoded_bytes=1000)
 def test_independent_readers(self):
  p,k=self.fixture();v=load(p,**k);a=v.reader();b=v.reader();self.assertEqual(a.read(4),b'abc\n');self.assertEqual(b.read(),b'abc\n'*100);self.assertEqual(v.decoded_bytes,400)
 def test_changed_source_cannot_change_accepted_bytes(self):
  p,k=self.fixture();v=load(p,**k);p.write_bytes(b'');self.assertEqual(v.reader().read(),b'abc\n'*100)
 def test_wrong_archive(self):
  p,k=self.fixture();k['archive_sha256']='0'*64
  with self.assertRaises(ValueError):load(p,**k)
 def test_wrong_member_hash(self):
  p,k=self.fixture();k['member_sha256']='0'*64
  with self.assertRaises(ValueError):load(p,**k)
 def test_duplicate(self):
  p,k=self.fixture(duplicate=True)
  with self.assertRaises(ValueError):load(p,**k)
 def test_truncated_gzip(self):
  p,k=self.fixture(gzip.compress(b'abc')[:-3])
  with self.assertRaises(EOFError):load(p,**k)
 def test_decoded_budget(self):
  p,k=self.fixture();k['max_decoded_bytes']=399
  with self.assertRaises(ValueError):load(p,**k)
 def test_compressed_budget(self):
  p,k=self.fixture();k['max_compressed_bytes']=1
  with self.assertRaises(ValueError):load(p,**k)
if __name__=='__main__':unittest.main()
