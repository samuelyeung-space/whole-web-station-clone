"""Schema and top-quote preservation tests; no financial claims."""
import unittest
from raw_clob_bbo_sidecar import envelope, top_quote


def quote():
    return dict(event_type='best_bid_ask', timestamp='1000', market='m',
                asset_id='up', best_bid='.73', best_ask='.76')


class NativeQuoteTests(unittest.TestCase):
    def test_legacy_receipt_and_native_source_are_distinct(self):
        ctx, messages = envelope(dict(recv_ms=1070, connection_epoch=1, sequence=10, msg=[quote()]))
        row = top_quote(messages[0], ctx)
        self.assertEqual(row['recv_ms'], 1070)
        self.assertEqual(row['source_ts_ms'], 1000)
        self.assertIsNone(row['connection_id'])
        self.assertFalse(row['depth_known'])
        self.assertNotIn('size', row)

    def test_modern_nanosecond_envelope(self):
        ctx, messages = envelope(dict(rn=1070000000, e=2, s=11, c=3, m=quote()))
        row = top_quote(messages[0], ctx)
        self.assertEqual(row['recv_ms'], 1070)
        self.assertEqual(row['connection_id'], 3)
        self.assertEqual(row['input_sequence'], 11)

    def test_missing_receipt_does_not_default_to_zero(self):
        with self.assertRaisesRegex(ValueError, 'missing explicit'):
            envelope(dict(connection_epoch=1, sequence=1, msg=[quote()]))

    def test_missing_epoch_or_sequence_fails(self):
        for item in (dict(recv_ms=1070, sequence=1, msg=[]),
                     dict(recv_ms=1070, connection_epoch=1, msg=[])):
            with self.assertRaises(ValueError):
                envelope(item)

    def test_boundaries_and_invalid_values(self):
        for bid, ask in [(.8,.7),(1,1),(0,0),(float('nan'),.9),(.2,float('inf'))]:
            message = quote(); message.update(best_bid=bid,best_ask=ask)
            with self.assertRaises(ValueError):
                top_quote(message, {})
        message = quote(); message.update(best_bid=0,best_ask=1)
        self.assertTrue(top_quote(message,{})['quote_only'])

    def test_non_quote_event_is_not_promoted(self):
        self.assertIsNone(top_quote(dict(event_type='last_trade_price'), {}))

    def test_missing_token_does_not_get_inferred(self):
        message=quote(); del message['asset_id']
        with self.assertRaises(ValueError):
            top_quote(message,{})


if __name__ == '__main__':
    unittest.main()
