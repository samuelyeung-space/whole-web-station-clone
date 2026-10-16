import copy
import tempfile
import unittest
from pathlib import Path
from replay_payloads import build, restore

class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.src=self.root/'src'; self.src.mkdir(); self.parts=self.root/'parts'
        (self.src/'tape.gz').write_bytes(bytes(range(256))*41)
        (self.src/'empty').write_bytes(b'')
        self.index=build(self.src,['tape.gz','empty'],self.parts,chunk_bytes=2048,max_part_bytes=6500)
    def test_split_roundtrip_and_idempotent(self):
        self.assertGreater(len(self.index['parts']),1)
        self.assertTrue(all(p['bytes']<=6500 for p in self.index['parts']))
        dst=self.root/'restored'; restore(self.index,self.parts,dst)
        self.assertEqual((self.src/'tape.gz').read_bytes(),(dst/'tape.gz').read_bytes())
        self.assertTrue(all(x['status']=='verified_existing' for x in restore(self.index,self.parts,dst)))
        again=build(self.src,['tape.gz','empty'],self.parts,self.index,2048,6500)
        self.assertEqual(self.index,again)
    def test_tampered_part_never_publishes_bad_file(self):
        p=self.parts/self.index['parts'][0]['name']; p.write_bytes(p.read_bytes()+b'x')
        with self.assertRaisesRegex(ValueError,'checksum/length'):
            restore(self.index,self.parts,self.root/'bad')
        self.assertFalse((self.root/'bad'/'tape.gz').exists())
    def test_tampered_chunk_and_final_hash(self):
        for field in ['chunk','final']:
            index=copy.deepcopy(self.index)
            if field=='chunk': index['files']['tape.gz']['chunks'][0]['sha256']='0'*64
            else: index['files']['tape.gz']['sha256']='0'*64
            with self.assertRaises(ValueError): restore(index,self.parts,self.root/field)
            self.assertFalse((self.root/field/'tape.gz').exists())
    def test_paths_and_existing_bytes(self):
        index=copy.deepcopy(self.index); index['files']['../escape']=index['files'].pop('tape.gz')
        with self.assertRaises(ValueError): restore(index,self.parts,self.root/'badpath')
        dst=self.root/'existing';dst.mkdir();(dst/'tape.gz').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'overwrite'): restore(self.index,self.parts,dst)
        self.assertEqual((dst/'tape.gz').read_bytes(),b'keep')
    def test_symlink_output(self):
        dst=self.root/'links';dst.mkdir();(dst/'tape.gz').symlink_to(self.src/'tape.gz')
        with self.assertRaisesRegex(ValueError,'escapes'): restore(self.index,self.parts,dst)
    def test_identical_files_share_chunks_without_duplicate_zip_members(self):
        (self.src/'duplicate.gz').write_bytes((self.src/'tape.gz').read_bytes())
        index=build(self.src,['tape.gz','duplicate.gz'],self.root/'dedup_parts',chunk_bytes=2048,max_part_bytes=6500)
        self.assertEqual(index['files']['tape.gz'],index['files']['duplicate.gz'])
        restore(index,self.root/'dedup_parts',self.root/'dedup_restored')
        self.assertEqual((self.root/'dedup_restored'/'tape.gz').read_bytes(),(self.root/'dedup_restored'/'duplicate.gz').read_bytes())

if __name__=='__main__':unittest.main()
