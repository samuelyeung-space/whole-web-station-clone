import unittest
from book_batch_intake import BatchGates
from absorption import TokenBook


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.d=BatchGates([dict(arm_id='a',asset_id='token',market_id='m',side='Up',decision_ms=250),
                           dict(arm_id='b',asset_id='token',market_id='m',side='Up',decision_ms=350)])
    def feed(self,t):
        self.d.before_group(t,1)
        b=TokenBook(bids={.49:25},asks={.51:175},recv_ms=t,ready=True)
        self.d.observe(dict(asset_id='token',book=b,recv_ms=t,epoch=1,update_id=t,validated=True,ambiguous_receipt=False))
    def test_independent_history_and_earlier_watch_not_rearmed(self):
        for t in [0,100,200,300,400]:self.feed(t)
        g=self.d.g
        self.assertEqual(g.arms['a']['arm_ms'],250);self.assertEqual(g.arms['b']['arm_ms'],350)
        self.assertIsNot(g.books['a']['history'],g.books['b']['history'])
        self.assertIsNot(g.books['a']['history'],g.books['token']['history'])
        self.assertEqual([q['time'] for q in g.books['a']['history']],[0,100,200,300,400])
    def test_lifecycle_cancels_all_overlapping_watches(self):
        for t in [0,100,200,300,400]:self.feed(t)
        self.d.before_group(500,2,True)
        self.assertEqual({r['status'] for r in self.d.g.arms.values()},{'cancel_source_lifecycle'})
    def test_equal_clock_parent_is_before_new_quote(self):
        self.d.before_group(250,1)
        self.assertEqual(self.d.g.arms['a']['status'],'rejected_missing_causal_baseline')
    def test_deadline_expires_without_filling(self):
        for t in [0,100,200,300,400]:self.feed(t)
        self.d.before_group(40000,1)
        self.assertEqual({r['status'] for r in self.d.g.arms.values()},{'timeout_unfilled'})
        self.assertEqual(self.d.gates,[])

    def test_shared_quote_fires_each_parent_once_without_duplicate_persistence(self):
        for t in [0,100,200,300,400]:self.feed(t)
        b=TokenBook(bids={.49:100},asks={.51:100},ready=True)
        for t in [500,749,750]:
            self.d.before_group(t,1)
            self.d.observe(dict(asset_id='token',book=b,recv_ms=t,epoch=1,update_id=t,validated=True,ambiguous_receipt=False))
        self.assertEqual([(e['arm_id'],e['recv_ms']) for e in sorted(self.d.gates,key=lambda e:e['arm_id'])],[('a',750),('b',750)])


if __name__=='__main__':unittest.main()
