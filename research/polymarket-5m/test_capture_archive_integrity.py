"""Synthetic archive fault cases only; not recording or profit evidence."""
import gzip,hashlib,io,json,tarfile,tempfile,unittest
from pathlib import Path
from capture_archive_integrity import audit_archive,sha


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'capture.tar'
        self.files={'capture-runtime.json':b'{"complete":true}\n',
                    'capture-health.json':b'{}\n',
                    'raw/2026-10-20/markets.jsonl.gz':gzip.compress(b'{"slug":"btc-updown-5m-1792454400"}\n'),
                    'raw/2026-10-20/clob-btc.jsonl.gz':gzip.compress(b'opaque, deliberately not JSON\n')}
    def tearDown(self):self.tmp.cleanup()
    def pack(self, *, wrong_hash=False, extra=False, duplicate=False, symlink=False, wrong_size=False):
        rows=[dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()) for name,data in self.files.items()]
        if wrong_hash:rows[-1]['sha256']='0'*64
        if wrong_size:rows[-1]['bytes']+=1
        manifest=dict(schema='capture-checkpoint-v1',run_id='synthetic',source_commit='synthetic',files=rows)
        with tarfile.open(self.path,'w') as tar:
            def add(name,data):
                m=tarfile.TarInfo(name);m.size=len(data);tar.addfile(m,io.BytesIO(data))
            add('capture-manifest.json',json.dumps(manifest).encode())
            for name,data in self.files.items():add(name,data)
            if extra:add('ledger.csv',b'unmanifested')
            if duplicate:add('capture-health.json',b'{}\n')
            if symlink:
                m=tarfile.TarInfo('linked');m.type=tarfile.SYMTYPE;m.linkname='capture-health.json';tar.addfile(m)
        return self.path.stat().st_size,sha(self.path)
    def assess(self,**kwargs):
        size,digest=self.pack(**kwargs);return audit_archive(self.path,digest,size)
    def test_complete_hash_and_crc_not_semantic_or_profit_admission(self):
        r=self.assess();self.assertEqual(r['btc5m_registered_markets'],1)
        self.assertEqual(len(r['gzip_streams']),2);self.assertTrue(r['all_gzip_eof_crc_verified'])
        self.assertFalse(r['raw_files_extracted']);self.assertFalse(r['prices_or_outcomes_semantically_read'])
        self.assertFalse(r['execution_input_admitted']);self.assertIsNone(r['formal_oos_result'])
    def test_bad_archive_digest_and_size(self):
        size,digest=self.pack()
        for h,n in [('0'*64,size),(digest,size+1)]:
            with self.assertRaisesRegex(ValueError,'archive size/hash'):audit_archive(self.path,h,n)
    def test_member_hash_mismatch(self):
        with self.assertRaisesRegex(ValueError,'member hash'):self.assess(wrong_hash=True)
    def test_member_size_mismatch(self):
        with self.assertRaisesRegex(ValueError,'member size'):self.assess(wrong_size=True)
    def test_unmanifested_file(self):
        with self.assertRaisesRegex(ValueError,'inventory'):self.assess(extra=True)
    def test_duplicate_member(self):
        with self.assertRaisesRegex(ValueError,'duplicate or nonregular'):self.assess(duplicate=True)
    def test_symlink(self):
        with self.assertRaisesRegex(ValueError,'duplicate or nonregular'):self.assess(symlink=True)
    def test_path_traversal_never_extracts(self):
        self.files['../escape']=b'no'
        with self.assertRaisesRegex(ValueError,'unsafe'):self.assess()
        self.assertFalse((self.path.parent.parent/'escape').exists())
    def test_bad_gzip_footer_with_correct_container_and_member_hashes(self):
        key='raw/2026-10-20/clob-btc.jsonl.gz';self.files[key]=self.files[key][:-8]
        with self.assertRaises((EOFError,OSError)):self.assess()
    def test_gzip_crc_failure_not_hidden_by_manifest_hash(self):
        key='raw/2026-10-20/clob-btc.jsonl.gz';data=bytearray(self.files[key]);data[-8]^=1;self.files[key]=bytes(data)
        with self.assertRaises((EOFError,OSError)):self.assess()
    def test_missing_newline_blocks(self):
        self.files['raw/2026-10-20/clob-btc.jsonl.gz']=gzip.compress(b'no terminal newline')
        with self.assertRaisesRegex(ValueError,'unterminated'):self.assess()
    def test_no_winner_fields_are_required_or_inferred(self):
        self.files['raw/2026-10-20/markets.jsonl.gz']=gzip.compress(b'{"slug":"other"}\n')
        r=self.assess();self.assertEqual(r['btc5m_registered_markets'],0)
        self.assertIsNone(r['seven_day_result']);self.assertIsNone(r['full_recorded_utc_days'])
    def test_zero_byte_gzip_name_is_not_a_valid_crc_stream(self):
        self.files['raw/2026-10-20/clob-btc.jsonl.gz']=b''
        with self.assertRaisesRegex(ValueError,'gzip header/footer'):self.assess()
    def test_real_empty_gzip_is_byte_valid_not_market_coverage(self):
        self.files['raw/2026-10-20/clob-btc.jsonl.gz']=gzip.compress(b'')
        r=self.assess();stream=next(x for x in r['gzip_streams'] if 'clob-btc' in x['path'])
        self.assertEqual(stream['records'],0);self.assertFalse(r['execution_input_admitted'])


if __name__=='__main__':unittest.main()
