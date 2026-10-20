"""Offline transport adversaries; no credential/network access."""
import hashlib,io,re,tempfile,unittest
from pathlib import Path
import artifact_range_download as a


class Response(io.BytesIO):
    def __init__(self,data,status,headers):super().__init__(data);self.status=status;self.headers=headers


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.target=Path(self.tmp.name)/'artifact.zip'
        self.data=b'0123456789abcdef'
        self.transfer=dict(download_url='https://prepared.example/secret-token',expected_bytes=len(self.data),
            expected_sha256=hashlib.sha256(self.data).hexdigest())
    def tearDown(self):self.tmp.cleanup()
    def opener(self,mode=None):
        def open_(req,timeout):
            lo,hi=map(int,re.fullmatch(r'bytes=(\d+)-(\d+)',req.get_header('Range')).groups())
            data=self.data[lo:hi+1];status=206
            headers={'Content-Range':f'bytes {lo}-{hi}/{len(self.data)}','Content-Length':str(len(data))}
            if mode=='ignored':status=200
            elif mode=='offset':headers['Content-Range']=f'bytes {lo+1}-{hi+1}/{len(self.data)}'
            elif mode=='total':headers['Content-Range']=f'bytes {lo}-{hi}/1000'
            elif mode=='length':headers['Content-Length']='100'
            elif mode=='short':data=data[:-1]
            elif mode=='long':data+=b'extra'
            return Response(data,status,headers)
        return open_
    def test_exact_parallel_ranges_reassemble_and_verify_full_sha(self):
        receipt=a.download(self.transfer,self.target,block_bytes=5,workers=2,opener=self.opener())
        self.assertEqual(self.target.read_bytes(),self.data)
        self.assertEqual(receipt['range_count'],4)
        self.assertTrue(receipt['sha256_verified'])
        self.assertFalse(self.target.with_name(self.target.name+'.pending').exists())
        self.assertNotIn('secret',str(receipt))
    def test_wrong_status_or_range_headers_cannot_certify_artifact(self):
        for mode in ('ignored','offset','total','length'):
            target=self.target.with_name(mode)
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                a.download(self.transfer,target,block_bytes=5,workers=2,opener=self.opener(mode))
            self.assertFalse(target.exists());self.assertTrue(target.with_name(mode+'.pending').exists())
    def test_short_or_excess_body_is_rejected(self):
        for mode in ('short','long'):
            target=self.target.with_name(mode)
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                a.download(self.transfer,target,block_bytes=5,workers=2,opener=self.opener(mode))
            self.assertFalse(target.exists())
    def test_correct_range_lengths_do_not_replace_full_hash_verification(self):
        bad={**self.transfer,'expected_sha256':'0'*64}
        with self.assertRaisesRegex(ValueError,'SHA256'):
            a.download(bad,self.target,block_bytes=5,opener=self.opener())
        self.assertFalse(self.target.exists())
    def test_existing_complete_or_pending_file_is_never_overwritten(self):
        for suffix in ('','.pending'):
            target=self.target.with_name('file'+suffix.replace('.','_'))
            protected=target.with_name(target.name+suffix);protected.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):a.download(self.transfer,target,opener=self.opener())
            self.assertEqual(protected.read_bytes(),b'keep')
    def test_size_digest_and_worker_configuration_are_bounded(self):
        for field,value in [('expected_bytes',True),('expected_bytes',0),('expected_bytes',513*1024**2),
            ('expected_sha256','not-a-sha')]:
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                a.download({**self.transfer,field:value},self.target,opener=self.opener())
        for kwargs in ({'workers':0},{'workers':9},{'block_bytes':0},{'block_bytes':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                a.download(self.transfer,self.target,opener=self.opener(),**kwargs)


if __name__=='__main__':unittest.main()
