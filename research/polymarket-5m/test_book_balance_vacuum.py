import unittest
from book_balance_vacuum import QuoteGates


class BookGateTests(unittest.TestCase):
    def setUp(self):
        self.d=QuoteGates()

    def book(self,t,bids=None,asks=None,**kw):
        return self.d.on_book('m',t,1,bids or {0.49:100},asks or {0.51:100},
                              update_id=t,**kw)

    def baseline(self,opposed=False):
        for t in [0,100,200]:
            self.book(t,{.49:25} if opposed else None,{.51:175} if opposed else None)
        self.assertTrue(self.d.arm('m',1,.50,200))

    def test_balance_requires_prior_pressure_and_sustained_neutrality(self):
        self.baseline(True)
        self.assertEqual(self.book(300),[])
        self.assertEqual(self.book(549),[])
        out=self.book(550)
        self.assertEqual(out[0]['status'],'balance_ready')
        self.assertTrue(self.d.gate('m',550))
        self.assertFalse(self.d.gate('m',551))

    def test_neutral_book_without_prior_pressure_is_not_a_trigger(self):
        self.baseline();self.book(300);self.book(600)
        self.assertFalse(self.d.gate('m',600))

    def test_balance_hold_resets_when_pressure_returns(self):
        self.baseline(True);self.book(300)
        self.book(500,{.49:25},{.51:175});self.book(600)
        self.assertEqual(self.book(849),[])
        self.assertEqual(self.book(850)[0]['status'],'balance_ready')

    def test_vacuum_needs_collapse_persistent_wall_overshoot_and_repair(self):
        self.baseline()
        # depth40<=50, spread.06>=.04, mid.43 is >3 prior spreads down.
        self.book(300,{.40:15,.39:5},{.46:20})
        self.book(450,{.40:15,.39:5},{.46:20})
        self.book(550,{.40:15,.39:5},{.46:20})
        self.assertFalse(self.d.gate('m',550,'vacuum'))
        # Same bid wall remains dominant while other liquidity returns.
        out=self.book(600,{.40:60,.39:40},{.42:100})
        # mid .41 still worsens, not repair.
        self.assertEqual(out,[])
        out=self.book(700,{.40:60,.39:40},{.42:100})
        self.assertEqual(out,[])  # flat is not repair either
        out=self.book(800,{.42:10,.40:60,.39:30},{.44:100})
        self.assertEqual(out[0]['status'],'vacuum_repair_ready')
        self.assertTrue(self.d.gate('m',800,'vacuum'))
        self.assertGreaterEqual(out[0]['wall_updates'],3)

    def test_ambiguous_empty_crossed_or_epoch_change_cancel(self):
        for mode in ['ambiguous','empty','crossed','epoch']:
            with self.subTest(mode=mode):
                self.d=QuoteGates();self.baseline(True)
                if mode=='ambiguous':self.book(300,ambiguous_receipt=True)
                elif mode=='empty':self.d.on_book('m',300,1,{}, {.51:10},update_id=300)
                elif mode=='crossed':self.book(300,{.52:10},{.51:10})
                else:self.d.on_book('m',300,2,{.49:10},{.51:10},update_id=300)
                self.assertTrue(self.d.arms['m']['status'].startswith('cancel_'))

    def test_duplicate_frame_does_not_advance_persistence(self):
        self.baseline(True);self.book(300)
        self.d.on_book('m',600,1,{.49:100},{.51:100},update_id=300)
        self.assertEqual(self.d.books['m']['time'],300)
        self.assertFalse(self.d.gate('m',600))

    def test_reused_update_id_cannot_hide_epoch_change(self):
        self.baseline(True)
        self.d.on_book('m',300,2,{.49:100},{.51:100},update_id=200)
        self.assertEqual(self.d.arms['m']['status'],'cancel_epoch_or_gap')

    def test_no_future_baseline_no_rearm_and_timeout(self):
        for t in [0,100,200]:self.book(t)
        self.assertFalse(self.d.arm('m',1,.5,199))
        self.assertFalse(self.d.arm('m',1,.5,200))
        self.d=QuoteGates();self.baseline(True)
        for t in range(1000,30001,1000):
            self.book(t,{.49:25},{.51:175})
        self.book(30200,{.49:25},{.51:175})
        self.assertEqual(self.d.arms['m']['status'],'timeout_unfilled')

    def test_timer_can_expire_without_a_new_book_but_cannot_fill(self):
        self.baseline(True);self.d.advance_time(30200)
        self.assertEqual(self.d.arms['m']['status'],'timeout_unfilled')
        self.assertFalse(self.d.gate('m',30200))


if __name__=='__main__':unittest.main(verbosity=2)
