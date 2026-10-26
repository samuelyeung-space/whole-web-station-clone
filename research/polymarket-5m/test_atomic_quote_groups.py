import unittest
from atomic_quote_groups import AtomicBooks,receipt_groups


def snap(t=0,epoch=1):
    return dict(kind='clob_snapshot',connection_epoch=epoch,recv_ms=t,asset_id='a',
                bids=[dict(price=.49,size=10)],asks=[dict(price=.51,size=10)])


def change(t,side,p,q,epoch=1):
    return dict(kind='clob_price_change',connection_epoch=epoch,recv_ms=t,asset_id='a',
                side=side,price=p,size=q)


class AtomicQuoteTests(unittest.TestCase):
    def test_transient_cross_inside_group_is_never_exposed(self):
        a=AtomicBooks();a.apply([snap()])
        x=a.apply([change(1,'BUY',.52,10),change(1,'SELL',.51,0),change(1,'SELL',.53,10)])[0]
        self.assertTrue(x['validated']);self.assertFalse(x['ambiguous_receipt'])
        self.assertEqual(max(x['book'].bids),.52)

    def test_cancel_readd_same_clock_invalidates_persistence(self):
        a=AtomicBooks();a.apply([snap()])
        x=a.apply([change(1,'BUY',.49,0),change(1,'BUY',.49,10)])[0]
        self.assertTrue(x['validated']);self.assertTrue(x['ambiguous_receipt'])

    def test_redundant_same_level_is_one_observation(self):
        a=AtomicBooks();a.apply([snap()]); r=change(1,'BUY',.49,20)
        x=a.apply([r,r])[0]
        self.assertFalse(x['ambiguous_receipt']);self.assertEqual(x['update_id'],(1,1))

    def test_epoch_cannot_inherit_old_book(self):
        a=AtomicBooks();a.apply([snap()])
        self.assertFalse(a.apply([change(1,'BUY',.49,20,epoch=2)])[0]['validated'])

    def test_lifecycle_discards_book_even_without_asset(self):
        a=AtomicBooks();a.apply([snap()])
        a.apply([dict(kind='clob_error',connection_epoch=1,recv_ms=1)])
        self.assertFalse(a.apply([change(2,'BUY',.49,20)])[0]['validated'])

    def test_boundary_delete_and_final_cross_fail_closed(self):
        for r in [change(1,'BUY',0,0),change(1,'BUY',.52,10)]:
            a=AtomicBooks();a.apply([snap()])
            self.assertFalse(a.apply([r])[0]['validated'])

    def test_mixed_snapshot_delta_is_ambiguous(self):
        a=AtomicBooks();self.assertTrue(a.apply([snap(),change(0,'BUY',.49,20)])[0]['ambiguous_receipt'])

    def test_grouping_keeps_exact_fractional_receipts_and_rejects_regression(self):
        self.assertEqual([len(x) for x in receipt_groups([snap(.1),snap(.1),snap(.2)])],[2,1])
        with self.assertRaises(ValueError):list(receipt_groups([snap(1),snap(.5)]))


if __name__=='__main__': unittest.main()
