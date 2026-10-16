"""Synthetic admission/counterexample tests; these are not market backtests."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import lake_parquet_adapter as lake
import replay_dataset

class LakeTests(unittest.TestCase):
    def test_no_equal_or_future_receipt(self):
        s=lake.Series([20,10],[[2],[1]])
        self.assertEqual(s.before(10,100)[2],'missing')
        self.assertEqual(s.before(20,100)[0][0],1)
        self.assertEqual(s.before(21,100)[0][0],2)
    def test_ambiguous_equal_receipt_invalidates_previous(self):
        s=lake.Series([10,20,20],[[1],[2],[3]])
        self.assertEqual(s.before(21,100)[2],'ambiguous_equal_receipt')
        self.assertEqual(s.before(20,100)[0][0],1)
    def test_exact_duplicate_and_missing_receipt(self):
        s=lake.Series([np.nan,10,10],[[5],[1],[1]])
        self.assertEqual(len(s.times),1)
        self.assertEqual(s.before(11,100)[0][0],1)
    def test_source_rewind_is_not_a_fresh_quote(self):
        counts={}
        from collections import Counter
        counts=Counter()
        s=lake.Series([10,20,30],[[1,10],[2,9],[3,11]],source_column=1,counts=counts)
        self.assertEqual(s.before(25,100)[0][0],1)
        self.assertEqual(s.before(25,100)[1],10)
        self.assertEqual(counts['rejected_source_time_regressions'],1)
    def test_equal_receipt_order_does_not_select_source_sequence(self):
        a=lake.Series([10,20,20],[[1,10],[2,12],[3,11]],source_column=1)
        b=lake.Series([10,20,20],[[1,10],[3,11],[2,12]],source_column=1)
        self.assertEqual(a.before(21,100)[2],'ambiguous_equal_receipt')
        self.assertEqual(b.before(21,100)[2],'ambiguous_equal_receipt')
    def test_missing_winner_numeric_not_guessed(self):
        with self.assertRaisesRegex(ValueError,'numeric encoding'):
            lake.canonical_market({'slot':300,'winner':1})
    def fixture(self,root):
        slot=300;books=[]
        # book_ok=False and resolved winner do not select/drop recordings.
        for n in range(81):
            r={'slot':slot,'ts_us':(slot*1000+n*100)*1000,'rcv_us':(slot*1000+n*100+1)*1000,'src':'synthetic'}
            for side in ['b','a']:
                for k in range(1,6):
                    r[f'{side}{k}p']=(.4-(k-1)*.01) if side=='b' else .5+(k-1)*.01
                    r[f'{side}{k}s']=5.
            books.append(r)
        pq.write_table(pa.Table.from_pylist(books),root/'books.parquet')
        pq.write_table(pa.Table.from_pylist([{'ts_us':t*1000,'rcv_us':t*1000+1,'price':100+t/1000000} for t in range(230000,310001,100)]),root/'spot.parquet')
        pq.write_table(pa.Table.from_pylist([{'slot':slot,'condition_id':'market','winner':'Up','book_ok':False}]),root/'markets.parquet')
        return root/'books.parquet',root/'spot.parquet',root/'markets.parquet'
    def test_normalization_is_explicit_up_only_and_causal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,s,m=self.fixture(root);out=root/'events.jsonl.gz'
            result=lake.normalize([b],[s],m,out,settlement_cutoff_ms=700000,assumed_fee_rate=.07,assumed_min_order_size=5,assumed_tick_size=.01)
            rows=[json.loads(x) for x in gzip.open(out,'rt')]
            self.assertEqual(result['registered_markets'],1)
            self.assertFalse(rows[0]['strict_execution_admitted'])
            self.assertEqual(rows[0]['execution_book_sides'],['Up'])
            books=[r for r in rows if r['type']=='book']
            self.assertTrue(books)
            self.assertTrue(all(r['down_asks']==[] and r['down_recv_ms'] is None and not r['healthy'] for r in books))
            self.assertTrue(all(r['up_recv_ms']<r['recv_ms'] for r in books if r['up_recv_ms'] is not None))
            decs=[r for r in rows if r['type']=='decision']
            self.assertTrue(any(r['features']['r_5s'] is not None for r in decs))
            self.assertTrue(all(r['features']['flow_15s'] is None for r in decs))
            for row in decs:
                self.assertTrue(all(t<=row['recv_ms'] for t in row['feature_available_ms'].values() if t is not None))
            outcome=next(r for r in rows if r['type']=='outcome')
            self.assertEqual(outcome['recv_ms'],700000)
            self.assertIsNone(outcome['local_outcome_recv_ms'])
            scanned=replay_dataset._scan(out)
            self.assertEqual(scanned['counts']['outcome'],1)
    def test_cutoff_cannot_release_early(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);b,s,m=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'later than every market end'):
                lake.normalize([b],[s],m,root/'x.gz',settlement_cutoff_ms=599999,assumed_fee_rate=.07,assumed_min_order_size=5,assumed_tick_size=.01)

if __name__=='__main__':unittest.main()
