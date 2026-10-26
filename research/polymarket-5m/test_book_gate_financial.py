import json,tempfile,unittest
from pathlib import Path
from book_gate_financial import merged_events


class FinancialProjectionTests(unittest.TestCase):
    def events(self,epoch=1,at=500):
        parent=dict(market_id='m',decision_ms=0,side='Down')
        token=dict(valid=True,recv_ms=0,bids=[(.49,100)],asks=[(.51,100)])
        projection={'requests':[
            dict(request_id='parent:m:0',at_ms=0,observed_horizon_covered=True,source_epoch=1,tokens={'u':token,'d':token}),
            dict(request_id='parent:m:500',at_ms=at,observed_horizon_covered=True,source_epoch=epoch,tokens={'u':token,'d':token})]}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'meta.jsonl';path.write_text('\n'.join(map(json.dumps,[
                dict(type='dataset',recv_ms=-2),dict(type='market',market_id='m',recv_ms=-1,start_ms=0,end_ms=300000,up_token_id='u',down_token_id='d',fee_rate=.07),
                dict(type='book',market_id='m',recv_ms=0,healthy=True,up_asks=[[.01,999]],down_asks=[[.01,999]]),
                dict(type='decision',market_id='m',recv_ms=0,features={'future':1})])))
            return list(merged_events(path,projection,[parent],{'gate_events':[]},'test'))

    def test_old_grid_books_decisions_are_excluded_and_side_clocks_preserved(self):
        events=self.events();books=[r for r in events if r['type']=='book']
        self.assertEqual(len(books),2);self.assertEqual(books[1]['down_asks'],[(.51,100)])
        self.assertEqual(books[1]['down_recv_ms'],0)
        decisions=[r for r in events if r['type']=='decision']
        self.assertEqual(len(decisions),1);self.assertEqual(decisions[0]['features']['experiment_side'],-1)

    def test_reconnect_cannot_rehabilitate_inflight_match(self):
        books=[r for r in self.events(epoch=2) if r['type']=='book']
        self.assertTrue(books[0]['healthy']);self.assertFalse(books[1]['healthy'])

    def test_wrong_projection_horizon_fails(self):
        with self.assertRaises(ValueError):self.events(at=600)


if __name__=='__main__':unittest.main()
