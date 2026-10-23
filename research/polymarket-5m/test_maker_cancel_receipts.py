"""Synthetic private-lifecycle cases, not market data or strategy backtests."""
from copy import deepcopy
import unittest
from maker_cancel_receipts import VERSION,reconcile


def trace(*changes):
    rows=[dict(type='capture',schema=VERSION,split='development',capture_id='synthetic',
               clock_id='recorder-clock',venue_sequence_scope='order_lifecycle')]
    def add(kind,**fields):
        row=dict(type=kind,local_sequence=len(rows),local_recv_ms=100*len(rows),
            capture_id='synthetic',clock_id='recorder-clock',raw_message_sha256='a'*64,
            order_id='order-1',market_id='market-1',token_id='up-token')
        row.update(fields);rows.append(row)
    add('submit',requested_shares=100,fixed_limit=.4,recorded_order_hold_usd=42,
        order_side='BUY',order_type='GTC',post_only=True)
    for kind,fields in changes:add(kind,**fields)
    return rows


class ReceiptTests(unittest.TestCase):
    def test_cancel_request_does_not_release_order_hold(self):
        r=reconcile(trace(('cancel_request',dict(cancel_id='c1'))))
        self.assertEqual(r['orders']['order-1']['unreconciled_order_hold_usd'],42)
        self.assertFalse(r['orders']['order-1']['terminal_quantity_reconciled'])
        self.assertIsNone(r['pnl_usd'])

    def test_older_venue_fill_received_after_cancel_ack_stays_at_local_receipt(self):
        r=reconcile(trace(('cancel_request',dict(cancel_id='c1')),
            ('cancel_ack',dict(cancel_id='c1',venue_sequence=20,cumulative_filled_shares=30,canceled_shares=70)),
            ('fill',dict(trade_id='t1',venue_sequence=19,filled_shares=30,fill_price=.39))))
        self.assertEqual(r['snapshots'][2]['received_filled_shares'],0)
        self.assertEqual(r['snapshots'][2]['unreconciled_order_hold_usd'],42)
        last=r['snapshots'][3]
        self.assertEqual(last['fill_received_after_cancel_ack_shares'],30)
        self.assertEqual(last['unreconciled_order_hold_usd'],0)
        self.assertEqual(last['local_recv_ms'],400)

    def test_cancel_rejection_and_missing_fill_receipts_remain_pending(self):
        for kind,fields in [('cancel_reject',dict(cancel_id='c1')),
            ('cancel_ack',dict(cancel_id='c1',venue_sequence=20,cumulative_filled_shares=30,canceled_shares=70))]:
            with self.subTest(kind=kind):
                r=reconcile(trace(('cancel_request',dict(cancel_id='c1')),(kind,fields)))
                self.assertEqual(r['orders']['order-1']['status'],'pending_private_receipts')

    def test_duplicate_fill_is_retained_without_double_count_and_conflict_blocks(self):
        f=('fill',dict(trade_id='t1',venue_sequence=19,filled_shares=30,fill_price=.39))
        rows=trace(f,f);r=reconcile(rows)
        self.assertEqual(r['orders']['order-1']['received_filled_shares'],30)
        self.assertEqual(r['counts']['duplicate_fill_receipts_retained'],1)
        rows[-1]['filled_shares']=31
        with self.assertRaisesRegex(ValueError,'conflicting duplicate'):reconcile(rows)

    def test_fixed_limit_and_terminal_venue_sequence_are_bound(self):
        for seq,price in [(21,.39),(19,.41)]:
            with self.subTest(seq=seq,price=price):
                with self.assertRaises(ValueError):reconcile(trace(
                    ('cancel_request',dict(cancel_id='c1')),
                    ('cancel_ack',dict(cancel_id='c1',venue_sequence=20,cumulative_filled_shares=30,canceled_shares=70)),
                    ('fill',dict(trade_id='t1',venue_sequence=seq,filled_shares=30,fill_price=price))))

    def test_receipt_identity_clock_and_complete_fill(self):
        rows=trace(('fill',dict(trade_id='t1',venue_sequence=2,filled_shares=100,fill_price=.4)))
        self.assertEqual(reconcile(rows)['orders']['order-1']['status'],'terminal_quantity_reconciled')
        for field,value in [('local_sequence',1),('local_recv_ms',0),('clock_id','other'),
                            ('token_id','down-token'),('venue_sequence',True)]:
            with self.subTest(field=field):
                changed=deepcopy(rows);changed[-1][field]=value
                with self.assertRaises(ValueError):reconcile(changed)
        for field,value in [('order_side','SELL'),('order_type','FAK'),('post_only',False)]:
            with self.subTest(intent_field=field):
                changed=deepcopy(rows);changed[1][field]=value
                with self.assertRaises(ValueError):reconcile(changed)


if __name__=='__main__':unittest.main()
