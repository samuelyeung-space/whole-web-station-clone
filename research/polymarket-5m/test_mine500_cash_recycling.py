import copy
import unittest
from mine500_cash_recycling import replay


def order(mid, decision, outcome, win=True, cost=60., shares=100.):
    return dict(market_id=mid, decision_ms=decision, match_ms=decision+500,
        submitted=True, requested_shares=100, reserved_usd_at_decision=65.,
        filled_shares=shares, cost_usd=cost, fees_usd=1.,
        settled=True, settlement_ms=outcome, payout_usd=shares if win else 0.,
        status='filled_full' if shares==100 else 'filled_partial')


class RecyclingTest(unittest.TestCase):
    def test_delayed_winner_cannot_finance_next_order(self):
        rows=[order('a',0,1000),order('b',2000,3000)]
        immediate,_,_=replay(rows,0,100)
        delayed,decisions,_=replay(rows,10,100)
        self.assertEqual(immediate['net_pnl_usd'],80)
        self.assertEqual(delayed['net_pnl_usd'],40)
        self.assertFalse(decisions[1]['admitted'])
        self.assertEqual(delayed['last_decision_snapshot']['payout_receivable_usd'],100)
        self.assertEqual(delayed['last_decision_snapshot']['cash_usd'],40)

    def test_pending_reservation_blocks_simultaneous_orders(self):
        x,ds,_=replay([order('a',0,2000),order('b',1,2000)],0,100)
        self.assertEqual(x['counts']['submitted'],1)
        self.assertFalse(ds[1]['admitted'])

    def test_cash_arriving_at_same_timestamp_conservatively_waits(self):
        x,ds,_=replay([order('a',0,1000),order('b',1000,2000)],0,100)
        self.assertFalse(ds[1]['admitted'])
        self.assertEqual(x['net_pnl_usd'],40)

    def test_outcome_does_not_change_earlier_admission(self):
        rows=[order('a',0,10000),order('b',1000,11000)]
        _,d1,_=replay(rows,0,100)
        changed=copy.deepcopy(rows);changed[0]['payout_usd']=0
        _,d2,_=replay(changed,0,100)
        self.assertEqual(d1,d2)

    def test_partial_fill_releases_unused_reserve(self):
        x,ds,_=replay([order('a',0,5000,cost=20,shares=30),order('b',1000,6000)],10,100)
        self.assertTrue(ds[1]['admitted'])
        self.assertEqual(x['filled_shares'],130)
        self.assertEqual(x['net_pnl_usd'],50)

    def test_loss_has_no_redemption_receivable(self):
        x,_,events=replay([order('a',0,1000,False)],86400,100)
        self.assertEqual(x['last_assumed_event_ms'],1000)
        self.assertEqual(x['net_pnl_usd'],-60)
        self.assertEqual(x['peak_pending_redemption_usd'],0)

    def test_no_fill_with_null_payout_releases_reservation(self):
        a=order('a',0,1000)
        a.update(filled_shares=0,cost_usd=0,fees_usd=0,payout_usd=None,
                 settled=False,status='no_fill')
        x,ds,_=replay([a,order('b',1000,2000)],86400,100)
        self.assertTrue(ds[1]['admitted'])
        self.assertEqual(x['counts']['no_fill'],1)
        self.assertEqual(x['net_pnl_usd'],40)

    def test_incomplete_counterfactual_or_duplicate_rejected(self):
        a=order('a',0,1000);a['submitted']=False
        with self.assertRaises(ValueError):replay([a],0)
        a['submitted']=True
        with self.assertRaises(ValueError):replay([a,a],0)
        a['settled']=False
        with self.assertRaises(ValueError):replay([a],0)


if __name__=='__main__':unittest.main()
