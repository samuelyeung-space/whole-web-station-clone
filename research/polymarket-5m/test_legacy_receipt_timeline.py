import gzip
import json
from pathlib import Path
import tempfile
import unittest
from legacy_recording_cache import envelope, sha
from legacy_receipt_timeline import build, Timeline

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root/'source'; self.src.mkdir()
        rows = [dict(recv_ms=300,asset_id='a',price=3),
                dict(recv_ms=100,asset_id='a',price=1),
                dict(recv_ms=200,asset_id='a',price=2),
                dict(recv_ms=200,asset_id='a',price=2.5),
                dict(source_ts=50,asset_id='a',winner='Up'),
                dict(recv_ms=300,asset_id='a',price=3)]
        with gzip.open(self.src/'s.gz','wt') as f:
            for i,r in enumerate(rows,1): f.write(json.dumps(envelope(r,'token_snapshot','original',i))+'\n')
        (self.src/'manifest.json').write_text(json.dumps(dict(schema='btc5m-legacy-lossless-cache-v1', total_rows=len(rows), streams=[dict(cache_file='s.gz',sha256=sha(self.src/'s.gz'),kind='token_snapshot',member='original',rows=len(rows))])))
        self.built = build(self.src,self.root/'index')
        self.t = Timeline(self.built['cache_path'])

    def tearDown(self):
        self.t.close(); self.temp.cleanup()

    def test_sorted_atomic_ties(self):
        batches = list(self.t.batches(0,301,['token_snapshot']))
        self.assertEqual([(t,len(rows)) for t,rows in batches],[(100,1),(200,2),(300,2)])

    def test_strict_before_no_future(self):
        self.assertEqual(self.t.asof('token_snapshot','a',100,100)['status'],'missing')
        self.assertEqual(self.t.asof('token_snapshot','a',200,100)['event']['raw']['price'],1)

    def test_conflicting_ties_not_last_wins(self):
        self.assertEqual(self.t.asof('token_snapshot','a',201,100)['status'],'ambiguous_equal_receipt')

    def test_identical_ties_keep_provenance(self):
        self.assertEqual(self.t.asof('token_snapshot','a',301,100)['duplicate_rows'],1)

    def test_stale_no_interpolation(self):
        self.assertEqual(self.t.asof('token_snapshot','a',500,100)['status'],'stale')

    def test_unclocked_preserved_not_admitted(self):
        self.assertEqual(self.built['manifest']['unclocked_rows'],1)
        self.assertEqual(self.t.db.execute('SELECT COUNT(*) FROM events WHERE recv IS NULL').fetchone()[0],1)
        self.assertEqual(sum(len(r) for _,r in self.t.batches(0,1000,['token_snapshot'])),5)

    def test_corruption_and_reuse(self):
        self.assertTrue(build(self.src,self.root/'index')['reused'])
        with (Path(self.built['cache_path'])/'timeline.sqlite').open('ab') as f:f.write(b'x')
        with self.assertRaises(ValueError):Timeline(self.built['cache_path'])

    def test_bad_query_clocks(self):
        for t in [True,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): self.t.asof('token_snapshot','a',t,100)

if __name__ == '__main__':unittest.main()
