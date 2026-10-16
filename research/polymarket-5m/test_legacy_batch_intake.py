import unittest
from legacy_batch_intake import coverage

class Tests(unittest.TestCase):
    def test_overlap_deduplicates_slots_and_markets_not_rows(self):
        m=dict(markets=[dict(slug='a',start_sec=1790812800)],streams=[dict(kind='token_snapshot',receipt_slot_row_counts={'1790812800':2}),dict(kind='btc_spot_trade',receipt_slot_row_counts={'1790812800':3})])
        result=coverage([dict(status='ingested',manifest=m)]*2)['2026-10-01']
        self.assertEqual(result['theoretical_5m_slots'],288)
        self.assertEqual(result['slots_with_any_book_receipt'],1)
        self.assertEqual(result['slots_without_book_receipts'],287)
        self.assertEqual(result['registered_market_ids'],1)
        self.assertEqual(result['book_rows'],4)
        self.assertIsNone(result['attempts']);self.assertIsNone(result['pnl'])

    def test_failed_archive_not_zero_pnl(self):
        self.assertEqual(coverage([dict(status='failed',error='CRC')]),{})

    def test_receipt_date_not_registry_date(self):
        m=dict(markets=[dict(slug='a',start_sec=1790812800)],streams=[dict(kind='token_snapshot',receipt_slot_row_counts={'1790899200':1})])
        result=coverage([dict(status='ingested',manifest=m)])
        self.assertEqual(result['2026-10-01']['slots_with_any_book_receipt'],0)
        self.assertEqual(result['2026-10-02']['registered_market_ids'],0)

if __name__=='__main__':unittest.main()
