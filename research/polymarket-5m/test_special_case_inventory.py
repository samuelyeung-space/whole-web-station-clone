import unittest
from special_case_inventory import InventoryAccount


def account(q=40,cost=20):return InventoryAccount(dict(side='Up',filled_shares=q,cost_usd=cost,match_ms=500))


class InventoryTests(unittest.TestCase):
    def test_zero_actual_entry_does_not_allow_exit_or_hedge(self):
        for path in ('SELL_HELD','BUY_COMPLEMENT'):
            a=account(0,0).fork(path);self.assertFalse(a.reserve('x',600,100,.5,.07)['submitted'])
            self.assertEqual(a.cash,1000)

    def test_independent_paths_and_partial_entry_caps(self):
        base=account();a=base.fork('SELL_HELD');b=base.fork('BUY_COMPLEMENT')
        self.assertEqual(b.reserve('x',600,100,.5,.07)['target_shares'],40)
        b.receipt('x',1100,10,.5,execution_validated=True)
        self.assertEqual(a.cash,980);self.assertEqual(base.positions['Down'],0)

    def test_pending_sell_prevents_oversell_and_cash_reuse(self):
        a=account().fork('SELL_HELD');a.reserve('x',600,100,.5,.07)
        a.receipt('x',1100,10,.6,execution_validated=True);self.assertEqual(a.cash,980)
        self.assertFalse(a.reserve('y',1150,100,.5,.07)['submitted'])
        a.acknowledge('x',1200);self.assertGreater(a.cash,985)
        self.assertEqual(a.reserve('y',1201,100,.5,.07)['target_shares'],30)

    def test_pending_hedge_cannot_reverse_inventory(self):
        a=account().fork('BUY_COMPLEMENT');a.reserve('x',600,100,.5,.07)
        self.assertFalse(a.reserve('y',700,100,.5,.07)['submitted'])
        a.receipt('x',1100,25,.4,execution_validated=True);a.acknowledge('x',1200)
        self.assertEqual(a.reserve('y',1300,100,.5,.07)['target_shares'],15)

    def test_bad_and_duplicate_receipts_leave_inventory_intact(self):
        a=account().fork('SELL_HELD');a.reserve('x',600,100,.6,.07)
        for p,v in [(.5,True),(.6,False)]:
            with self.assertRaises(ValueError):a.receipt('x',1100,10,p,execution_validated=v)
        self.assertEqual(a.positions['Up'],40)
        a.receipt('x',1100,10,.6,execution_validated=True)
        with self.assertRaises(ValueError):a.receipt('x',1100,10,.6,execution_validated=True)

    def test_partial_buy_keeps_unspent_reservation_until_ack(self):
        a=account().fork('BUY_COMPLEMENT');a.reserve('x',600,40,.5,.07);before=a.available()
        a.receipt('x',1100,10,.4,execution_validated=True);self.assertAlmostEqual(a.available(),before)
        a.acknowledge('x',1200);self.assertGreater(a.available(),before)

    def test_official_payout_release_and_no_double_settlement(self):
        a=account().fork('BUY_COMPLEMENT');a.reserve('x',600,40,.5,.07)
        a.receipt('x',1100,40,.4,execution_validated=True);a.acknowledge('x',1200)
        before=a.cash;a.settle('Down',2000,1800,2500);self.assertEqual(a.cash,before)
        with self.assertRaises(ValueError):a.release(2499)
        a.release(2500);self.assertEqual(a.cash,before+40)
        with self.assertRaises(ValueError):a.release(2600)

    def test_no_borrowing_and_clock_regression(self):
        a=account(40,999).fork('BUY_COMPLEMENT')
        self.assertEqual(a.reserve('x',600,40,.5,.07)['status'],'cash_reject')
        with self.assertRaises(ValueError):a.reserve('y',499,1,.5,.07)


if __name__=='__main__':unittest.main()
