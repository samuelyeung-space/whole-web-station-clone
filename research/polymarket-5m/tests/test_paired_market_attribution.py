import unittest
from paired_market_attribution import compare


def row(mid,pnl=0,**kw):
    r=dict(market_id=mid,pnl=pnl,sent=True,filled=True,winner='Up',observation_run_id='1',day=None,signal_receive_ms=1792780349443.727,evaluation_ms=500)
    r.update(kw);return r


class PairedTests(unittest.TestCase):
    def test_pair_before_sum_and_delete_whole_market(self):
        x=compare([row('a',2),row('b',-3)],[row('a',4),row('a',1),row('b',-4)])['by_latency']['500']
        self.assertEqual(x['delta_pnl'],2)
        self.assertEqual(x['delta_without_best_positive_market'],-1)
        self.assertEqual(x['sent_market_union'],2)
    def test_absent_arm_is_zero_only_for_complete_sent_union(self):
        x=compare([row('a',-2)],[row('b',3)])['by_latency']['500']
        self.assertEqual(x['delta_pnl'],5);self.assertEqual(x['sent_market_union'],2)
    def test_no_positive_delta_does_not_delete_loss(self):
        x=compare([row('a',2)],[row('a',1)])['by_latency']['500']
        self.assertEqual(x['delta_without_top3_positive_markets'],-1)
    def test_unresolved_rejected(self):
        with self.assertRaises(ValueError):compare([row('a',winner=None)],[row('a')])
    def test_conflicting_runs_and_labels_rejected(self):
        with self.assertRaises(ValueError):compare([row('a')],[row('a',observation_run_id='2')])
        with self.assertRaises(ValueError):compare([row('a')],[row('a',winner='Down')])


if __name__=='__main__':unittest.main()
