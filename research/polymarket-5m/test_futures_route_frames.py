import base64
import hashlib
import json
import unittest
from futures_route_frames import RouteFrameObserver


def message(**changes):
    d = dict(e='trade', E=1000, T=999, s='BTCUSDT', t=7, p='80000', q='0.01', m=False,
             future_field={'preserve': True})
    d.update(changes)
    return json.dumps(d)


class TestRouteFrames(unittest.TestCase):
    def setUp(self):
        self.observer = RouteFrameObserver(capture_id='synthetic', clock_id='clock-A',
                                          routes={'a': 'endpoint-A', 'b': 'endpoint-B'},
                                          split='development')
        self.writes, self.forwarded = [], []

    def record(self, raw, recv=1100, route='a', epoch=1, seq=0, writer=None, forward=None):
        return self.observer.record_and_forward(raw, recv_ms=recv, route_id=route,
            conn_id=0, epoch=epoch, sequence=seq,
            writer=writer or self.writes.append,
            forward=forward or (lambda *args: self.forwarded.append(args)))

    def test_zero_first_good_later_never_rewrites_first(self):
        zero = self.record(message(p='0', q='0'))
        saved_first = self.writes[0]
        good = self.record(message(), recv=1150, route='b')
        self.assertFalse(zero['classification']['economics_valid'])
        self.assertTrue(good['classification']['economics_valid'])
        self.assertEqual(good['comparison']['status'], 'unresolved_payload_conflict')
        self.assertEqual(good['comparison']['available_recv_ms'], 1150)
        self.assertEqual(self.writes[0], saved_first)
        self.assertEqual(len(self.forwarded), 2)
        self.assertFalse(good['coverage_certified'])
        self.assertEqual(good['features_admitted'], 0)

    def test_combined_copy_and_unknown_fields_are_byte_preserved(self):
        direct = message()
        combined = json.dumps({'stream': 'btcusdt@trade', 'data': json.loads(direct),
                               'future_envelope': 'keep'})
        self.record(direct)
        row = self.record(combined, route='b', recv=1150)
        self.assertEqual(row['comparison']['status'], 'repeat')
        self.assertEqual(base64.b64decode(row['raw_base64']), combined.encode())
        self.assertEqual(row['raw_sha256'], hashlib.sha256(combined.encode()).hexdigest())
        self.assertEqual(self.forwarded[-1][0], combined)

    def test_invalid_and_control_frames_are_retained_not_invented(self):
        values = [b'\xff', '{bad', '{"e":"trade","e":"aggTrade"}',
                  '{"x":NaN}', '{"x":1e999}', '{"result":null,"id":1}',
                  message(e='aggTrade'), message(m=None), message(p=True),
                  message().replace('"q": "0.01"', '"q": 1e999')]
        for i, raw in enumerate(values):
            row = self.record(raw, recv=1100+i, seq=i)
            self.assertFalse(row['classification'].get('economics_valid', False))
            self.assertEqual(base64.b64decode(row['raw_base64']), raw if isinstance(raw, bytes) else raw.encode())
        self.assertEqual(len(self.writes), len(values))

    def test_sink_failure_prevents_callback_and_latches(self):
        def fail(_):
            raise OSError('synthetic full disk')
        with self.assertRaises(OSError):
            self.record(message(), writer=fail)
        self.assertFalse(self.forwarded)
        with self.assertRaisesRegex(ValueError, 'failed closed'):
            self.record(message())

    def test_callback_failure_still_retains_original_and_receipt(self):
        def fail(*_):
            raise RuntimeError('synthetic downstream failure')
        with self.assertRaises(RuntimeError):
            self.record(message(), forward=fail)
        self.assertEqual(len(self.writes), 1)
        row = self.record(message(), recv=1150, seq=1)
        self.assertEqual(row['comparison']['first_frame_index'], 0)

    def test_route_sequence_and_clock_fail_without_forwarding(self):
        self.record(message())
        for changes in ({'recv': 1099, 'seq': 1}, {'seq': 2}, {'route': 'unknown'}):
            with self.assertRaises(ValueError):
                self.record(message(), **changes)
        self.assertEqual(len(self.writes), 1)
        self.record(message(), recv=1200, epoch=2)
        self.assertEqual(len(self.writes), 2)

    def test_long_decimals_do_not_merge_under_default_precision(self):
        self.record(message(p='123456789012345678901234567890.1'))
        row = self.record(message(p='123456789012345678901234567890.2'), recv=1200, route='b')
        self.assertFalse(row['comparison']['same_valid_economics'])
        self.assertEqual(row['comparison']['status'], 'unresolved_payload_conflict')

    def test_returned_row_cannot_rewrite_first_economic_signature(self):
        first = self.record(message())
        first['classification']['economic_signature'][1] = 'fake'
        row = self.record(message(E=1001), recv=1200, route='b')
        self.assertTrue(row['comparison']['same_valid_economics'])
        self.assertEqual(row['comparison']['status'], 'metadata_changed')


if __name__ == '__main__':
    unittest.main()
