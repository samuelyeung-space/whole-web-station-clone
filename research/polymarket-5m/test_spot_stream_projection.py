from collections import Counter
import unittest
import gzip,json,tempfile
from pathlib import Path
from spot_stream_projection import project_rows,project

def row(seq,stream='spot',recv=None,kind='spot_trade'):
    return {'seq':seq,'recv_ms':seq if recv is None else recv,'stream':stream,'kind':kind,'price':1,'size':1}

class Tests(unittest.TestCase):
    def test_preserves_parent_sequence_and_absence(self):
        a=list(project_rows([row(0,'futures',kind='futures_bbo'),row(1)],Counter()))[0]
        self.assertEqual((a['parent_seq'],a['seq']),(1,0));self.assertNotIn('aggressor_side',a)
    def test_unrelated_zero_price_is_counted_not_selected(self):
        r=row(0,'futures_trade',kind='futures_trade');r['price']=0;c=Counter()
        self.assertEqual(len(list(project_rows([r,row(1)],c))),1)
        self.assertEqual(c['excluded_nonpositive_or_missing_futures_price'],1)
    def test_parent_gap_fails_even_when_excluded(self):
        with self.assertRaises(ValueError):list(project_rows([row(1,'futures')],Counter()))
    def test_parent_regression_fails_even_when_excluded(self):
        with self.assertRaises(ValueError):list(project_rows([row(0,recv=2),row(1,'futures',recv=1)],Counter()))
    def test_unknown_stream_fails(self):
        with self.assertRaises(ValueError):list(project_rows([row(0,'unknown')],Counter()))
    def test_unknown_spot_event_fails(self):
        with self.assertRaises(ValueError):list(project_rows([row(0,kind='unexpected')],Counter()))
    def test_bad_clock_fails(self):
        for clock in (float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):list(project_rows([row(0,recv=clock)],Counter()))
    def test_complete_projection_and_side_missing(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'raw.gz';out=Path(d)/'projection';events=[]
            for i,kind in enumerate(('spot_connection','spot_trade','spot_disconnect')):
                e=row(i,kind=kind);e.update(connection_epoch=1,stream_sequence=i,source_ts_ms=i)
                events.append(e)
            with gzip.open(src,'wt') as f:
                for e in events:f.write(json.dumps(e)+'\n')
            r=project(src,out)
            self.assertEqual(r['validated_counts']['spot_trade'],1)
            self.assertEqual(r['counts']['spot_field_aggressor_side'],0)
            self.assertTrue((out/'manifest.json').is_file())
    def test_invalid_selected_event_never_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'raw.gz';out=Path(d)/'projection'
            with gzip.open(src,'wt') as f:f.write(json.dumps(row(0))+'\n')
            with self.assertRaises(ValueError):project(src,out)
            self.assertFalse((out/'manifest.json').exists())

if __name__=='__main__':unittest.main()
