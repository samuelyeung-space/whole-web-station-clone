import unittest
from special_case_entry_execution import tape


class EntryTapeTests(unittest.TestCase):
    def rows(self):
        yield dict(type='dataset',recv_ms=-1,book_resolution='decision_and_match_projection',decision_grid_ms=1000,supported_latencies_ms=[500,600,1000])
        yield dict(type='outcome',market_id='m',recv_ms=0,winner='Up')
        yield dict(type='decision',market_id='m',recv_ms=0,features={'future':1})
        for t in [0,500,600,1000]:yield dict(type='book',market_id='m',recv_ms=t)

    def intent(self):return dict(at_ms=0,market_id='m',side='Down',market_end_ms=300000)

    def test_one_frozen_decision_without_labels_or_old_signals(self):
        x=list(tape(self.rows(),self.intent(),'test'));self.assertFalse(any(r['type']=='outcome' for r in x))
        d=[r for r in x if r['type']=='decision'];self.assertEqual(len(d),1)
        self.assertEqual(d[0]['features'],dict(tau_s=300,entry_side=-1))

    def test_missing_horizon_fails_closed(self):
        with self.assertRaises(ValueError):list(tape((r for r in self.rows() if r['recv_ms']!=600),self.intent(),'test'))

    def test_offgrid_entry_cannot_reuse_old_projection(self):
        i=self.intent();i['at_ms']=1
        with self.assertRaises(ValueError):list(tape(self.rows(),i,'test'))


if __name__=='__main__':unittest.main()
