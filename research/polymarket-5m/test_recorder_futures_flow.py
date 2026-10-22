"""Engineering counterexamples; these are not economic strategy tests."""
import unittest
from copy import deepcopy
from recorder_futures_flow import CONTRACT, EXCHANGE, INSTRUMENT, RecorderFuturesFlowTape, recorder_receipt
from historical_flow_receipts import CoverageInterval
from binance_native_flow import NativeFlowTape


def row(**kw):
    r = dict(kind='trade', s='BTCUSDT', recv_ms=60000, T=59999, E=59999,
             t=9007199254740993, p='100', q='2', m=False, connection_epoch=1)
    r.update(kw); return r


ARGS = dict(capture_run_id='run:epoch1', recorder_contract=CONTRACT,
            time_unit='MILLISECOND', split='development')


class RecorderFuturesTests(unittest.TestCase):
    def test_exact_id_raw_row_and_direction(self):
        raw = row(); original = deepcopy(raw)
        a = recorder_receipt(raw, **ARGS)
        self.assertEqual(a['row']['trade_id'], 'trade:9007199254740993')
        self.assertEqual(a['row']['taker_side'], 'buy')
        self.assertFalse(a['native_event_field_retained'])
        self.assertEqual(a['raw_recorder_row'], original)
        self.assertNotIn('e', raw)
        self.assertEqual(recorder_receipt(row(m=True), **ARGS)['row']['taker_side'], 'sell')

    def test_routing_units_id_and_side_rejections(self):
        for change in [dict(t=1.0), dict(t=True), dict(m='false'), dict(kind='bookTicker'),
                       dict(s='ETHUSDT'), dict(e='trade'), dict(a=12), dict(q='0'),
                       dict(p='-1'), dict(q='NaN'), dict(recv_ms=True), dict(E=1000000)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                recorder_receipt(row(**change), **ARGS)
        for change in [dict(time_unit='MICROSECOND'), dict(split='OOS'),
                       dict(recorder_contract='unbound'), dict(capture_run_id='')]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                recorder_receipt(row(), **(ARGS | change))

    def test_missing_side_not_zero_or_tick_side(self):
        tape = RecorderFuturesFlowTape(**ARGS); tape.add_trade(row(m=None))
        tape.add_coverage(CoverageInterval(EXCHANGE, INSTRUMENT, 'run:epoch1', 0, 60000,
                                          60000, 'complete', 'synthetic engineering fixture'))
        self.assertIsNone(tape.features(60000)['features']['flow_15s'])
        self.assertEqual(tape.features(60000)['audit']['flow_15s']['status'], 'unknown_aggressor_side')

    def test_no_coverage_and_no_spot_substitution(self):
        tape = RecorderFuturesFlowTape(**ARGS); tape.add_trade(row())
        self.assertIsNone(tape.features(60000)['features']['flow_60s'])
        self.assertNotIsInstance(tape, NativeFlowTape)
        with self.assertRaises(ValueError):
            tape.add_coverage(CoverageInterval('binance', INSTRUMENT, 'run:epoch1', 0,
                                              60000, 60000, 'complete', 'wrong venue'))

    def test_future_receipt_and_coverage_do_not_rewrite_decision(self):
        tape = RecorderFuturesFlowTape(**ARGS); tape.add_trade(row())
        before = tape.features(60000)
        tape.add_trade(row(t=2, recv_ms=60001, T=60001, E=60001, m=True))
        tape.add_coverage(CoverageInterval(EXCHANGE, INSTRUMENT, 'run:epoch1', 0, 60000,
                                          60001, 'complete', 'later audit fixture'))
        self.assertEqual(before, tape.features(60000))

    def test_duplicate_and_exact_decimal_conflict(self):
        tape = RecorderFuturesFlowTape(**ARGS)
        tape.add_trade(row(p='100.00000000000000001'))
        tape.add_trade(row(recv_ms=60001, p='100.000000000000000010'))
        self.assertEqual(tape.engine.duplicate_receipts, 1)
        with self.assertRaises(ValueError):
            tape.add_trade(row(recv_ms=60002, p='100.00000000000000002'))
        self.assertIsNone(tape.features(60002)['features']['flow_15s'])

    def test_latched_failure_cannot_be_caught_and_skipped(self):
        tape = RecorderFuturesFlowTape(**ARGS); tape.add_trade(row())
        before = tape.features(60000)
        with self.assertRaises(ValueError): tape.add_trade(row(t=2, q='0', recv_ms=60001))
        self.assertEqual(before, tape.features(60000))
        with self.assertRaises(ValueError): tape.add_trade(row(t=3, recv_ms=60002))
        self.assertEqual(tape.features(60002)['audit']['flow_15s']['status'],
                         'blocked_recorder_ingestion_error')

    def test_backwards_clock_blocks_retrospective_queries(self):
        tape = RecorderFuturesFlowTape(**ARGS); tape.add_trade(row())
        with self.assertRaises(ValueError): tape.add_trade(row(t=2, recv_ms=59999))
        self.assertEqual(tape.features(50000)['audit']['flow_15s']['status'],
                         'blocked_recorder_ingestion_error')

    def test_epoch_change_does_not_bridge_reconnect(self):
        tape = RecorderFuturesFlowTape(**ARGS); tape.add_trade(row())
        with self.assertRaises(ValueError):
            tape.add_trade(row(t=2, recv_ms=60001, connection_epoch=2))
        self.assertIsNone(tape.features(60002)['features']['flow_15s'])
        for value in (None, 0, True, 1.0):
            with self.subTest(epoch=value), self.assertRaises(ValueError):
                recorder_receipt(row(connection_epoch=value), **ARGS)

    def test_reuse_existing_quote_volume_engine(self):
        tape = RecorderFuturesFlowTape(**ARGS)
        tape.add_trade(row(recv_ms=59900, T=59899, E=59899, m=False, p='100', q='2'))
        tape.add_trade(row(t=2, m=True, p='100', q='1'))
        tape.add_coverage(CoverageInterval(EXCHANGE, INSTRUMENT, 'run:epoch1', 0, 60000,
                                          60000, 'complete', 'synthetic engineering fixture'))
        self.assertAlmostEqual(tape.features(60000)['features']['flow_60s'], 1/3)


if __name__ == '__main__': unittest.main()
