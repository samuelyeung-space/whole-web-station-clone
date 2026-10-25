"""Synthetic causal admission fixtures; not historical backtests."""
import unittest
from historical_flow_receipts import CoverageInterval
from recorder_futures_flow import CONTRACT, EXCHANGE, INSTRUMENT, RecorderFuturesFlowTape
from binance_native_flow import NativeFlowTape
import usdm_flow_candidates as u


class FuturesRules(unittest.TestCase):
    def tape(self, prints, coverage=True):
        t = RecorderFuturesFlowTape(capture_run_id='fixture', recorder_contract=CONTRACT,
                                   time_unit='MILLISECOND', split='development')
        for i, (recv, quantity, maker) in enumerate(prints):
            t.add_trade(dict(kind='trade', s='BTCUSDT', recv_ms=recv, T=recv, E=recv,
                             t=i+1, p='100', q=str(quantity), m=maker, connection_epoch=1))
        if coverage:
            t.add_coverage(CoverageInterval(EXCHANGE, INSTRUMENT, 'fixture', 0, 60000,
                                           60000, 'complete', 'synthetic fixture only'))
        return t

    def evaluate(self, tape, **changes):
        args = dict(decision_recv_ms=60000, capture_clock_id='fixture:recv_ms',
                    tau_evidence=dict(value=20, as_of_ms=60000, available_ms=60000,
                                      capture_clock_id='fixture:recv_ms', provenance='synthetic tau'),
                    split='development')
        args.update(changes)
        return u.evaluate_decision(tape, **args)

    def test_registration_stable_explicit_venue_and_default_parity(self):
        a = u.candidates(); self.assertEqual(a, u.candidates())
        self.assertEqual(len({x['rule_fingerprint'] for x in a}), 4)
        for c in a:
            self.assertEqual(c['venue'], 'binance_usdm')
            self.assertFalse(c['independent_mechanism_claim'])
            self.assertIsNone(c['execution_binding'])
            self.assertNotEqual(c['rule_fingerprint'], c['source_rule_fingerprint'])

    def test_four_rule_directions_from_quote_volume(self):
        fixtures = [('flow_persistence', [(10000,1,False),(59000,1,False)], 'Up'),
                    ('flow_acceleration', [(10000,2,True),(59000,3,False)], 'Up'),
                    ('flow_exhaustion', [(10000,6,False),(58000,3,False),(59000,2,True)], 'Down'),
                    ('flow_flip', [(10000,9,True),(59000,1,False)], 'Up')]
        for name, prints, expected in fixtures:
            with self.subTest(template=name):
                r = self.evaluate(self.tape(prints))
                self.assertEqual(next(x['direction'] for x in r['rows'] if x['template']==name), expected)
                self.assertEqual(r['actual_backtests_performed'], 0)
                self.assertIsNone(r['pnl_usd'])

    def test_prints_without_coverage_do_not_admit(self):
        r = self.evaluate(self.tape([(59000,1,False)], coverage=False))
        self.assertTrue(all(x['status']=='blocked_features' for x in r['rows']))

    def test_missing_side_remains_unknown(self):
        r = self.evaluate(self.tape([(59000,1,None)]))
        self.assertTrue(all(x['direction'] is None for x in r['rows']))

    def test_future_coverage_and_future_print_preserve_old_decision(self):
        t = self.tape([(59000,1,False)], coverage=False); before = self.evaluate(t)
        t.add_coverage(CoverageInterval(EXCHANGE, INSTRUMENT, 'fixture', 0,60000,60001,
                                       'complete','later synthetic evidence'))
        t.add_trade(dict(kind='trade',s='BTCUSDT',recv_ms=60001,T=60001,E=60001,t=2,
                         p='100',q='8',m=True,connection_epoch=1))
        self.assertEqual(before, self.evaluate(t))

    def test_stale_future_tau_and_wrong_clock_block(self):
        t = self.tape([(59000,1,False)])
        for field, value in [('as_of_ms',59999),('available_ms',60001),('capture_clock_id','spot')]:
            tau=dict(value=20,as_of_ms=60000,available_ms=60000,capture_clock_id='fixture:recv_ms',provenance='fixture')
            tau[field]=value;r=self.evaluate(t,tau_evidence=tau)
            self.assertTrue(all(x['status']=='blocked_features' for x in r['rows']))

    def test_reject_wrong_tape_and_oos_adoption(self):
        with self.assertRaises(ValueError): self.evaluate(object())
        with self.assertRaises(ValueError):
            self.evaluate(NativeFlowTape(stream_kind='trade', time_unit='MILLISECOND'))
        with self.assertRaises(ValueError): self.evaluate(self.tape([]),split='OOS')
        with self.assertRaises(ValueError): self.evaluate(self.tape([]),capture_clock_id='other')

    def test_corrupt_print_blocks_future_admission(self):
        t = self.tape([(59000,1,False)])
        with self.assertRaises(ValueError):
            t.add_trade(dict(kind='trade',s='BTCUSDT',recv_ms=59500,T=59500,E=59500,
                             t=2,p='100',q='0',m=False,connection_epoch=1))
        r = self.evaluate(t)
        self.assertTrue(all(x['status']=='blocked_features' for x in r['rows']))

    def test_valid_no_signal_is_not_missing(self):
        r = self.evaluate(self.tape([(58000,1,False),(59000,1,True)]))
        self.assertTrue(all(x['status']=='rule_not_triggered' for x in r['rows']))
        self.assertTrue(all(x['blockers']=={} for x in r['rows']))


if __name__ == '__main__': unittest.main()
