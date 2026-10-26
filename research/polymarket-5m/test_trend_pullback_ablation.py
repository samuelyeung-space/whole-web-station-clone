"""Causal signal-state checks; no financial, FAK or partial-fill claims."""
from copy import deepcopy
import unittest
import trend_pullback_ablation as m


def features(**changes):
    return dict(tau_s=150., r_5s=1.125, r_15s=3., ctx_ma_direction=1,
                ctx_st_direction=1, ctx_price=100000., ctx_r1_bps=.1,
                ctx_epoch=1, ctx_market_start_ms=0, ctx_decision_ms=1000,
                ctx_ask_up=.60, ctx_ask_down=.41) | changes


class StateTests(unittest.TestCase):
    def setUp(self):
        self.candidates = {c['arm']: c for c in m.catalog()}
        self.machine = m.SignalMachine()

    def send(self, arm='WAIT', **changes):
        return self.machine.signal(self.candidates[arm], features(**changes))

    def pull(self):
        self.assertIsNone(self.send())
        self.assertIsNone(self.send(ctx_decision_ms=2000, ctx_price=99995., r_5s=-.4,
                                    ctx_r1_bps=-.4, ctx_ask_up=.58))

    def test_parent_boundaries_and_direction(self):
        self.assertEqual(m.parent_direction(features(tau_s=120)), 1)
        for change in ({'tau_s':180}, {'tau_s':119.9}, {'r_5s':1.1249},
                       {'r_15s':-2}, {'r_15s':None}):
            self.assertEqual(m.parent_direction(features(**change)), 0)
        self.assertEqual(m.parent_direction(features(r_5s=-1.125, r_15s=-3)), -1)

    def test_reference_does_not_need_new_context(self):
        self.assertEqual(self.machine.signal(self.candidates['REFERENCE'],
                         {'tau_s':150,'r_5s':1.125,'r_15s':3}), 'Up')

    def test_control_and_regime_filters(self):
        self.assertEqual(self.send('CONTROL',ctx_ma_direction=-1), 'Up')
        self.assertIsNone(self.send('MA10',ctx_ma_direction=-1))
        # First signal consumed: cannot wait for a later good regime.
        self.assertIsNone(self.send('MA10',ctx_decision_ms=2000))
        self.assertEqual(self.send('ST10'), 'Up')

    def test_cheaper_quote_after_observed_pullback_and_later_recovery(self):
        self.pull()
        # r5 is still negative: no instantaneous 108 AND contradiction.
        self.assertEqual(self.send(ctx_decision_ms=3000,ctx_price=99996.,r_5s=-.2,
                                  ctx_r1_bps=.1,ctx_ask_up=.59), 'Up')
        self.assertIsNone(self.send(ctx_decision_ms=4000,ctx_price=99997.,ctx_ask_up=.58))

    def test_downtrend_is_the_exact_price_side_mirror(self):
        common=dict(r_5s=-1.125,r_15s=-3.,ctx_ma_direction=-1,
                    ctx_st_direction=-1,ctx_r1_bps=-.1,ctx_ask_down=.60)
        self.assertIsNone(self.send(**common))
        self.assertIsNone(self.send(**(common|dict(ctx_decision_ms=2000,
            ctx_price=100005.,r_5s=.4,ctx_r1_bps=.4,ctx_ask_down=.58))))
        self.assertEqual(self.send(**(common|dict(ctx_decision_ms=3000,
            ctx_price=100004.,r_5s=.2,ctx_r1_bps=-.1,ctx_ask_down=.59))), 'Down')

    def test_discount_is_anchored_to_original_direct_ask(self):
        self.pull()
        self.assertIsNone(self.send(ctx_decision_ms=3000,ctx_price=99996.,ctx_ask_up=.591))
        self.assertEqual(self.send(ctx_decision_ms=4000,ctx_price=99997.,ctx_ask_up=.59), 'Up')

    def test_no_same_observation_pullback_and_confirmation(self):
        self.send()
        self.assertIsNone(self.send(ctx_decision_ms=2000,ctx_price=99995.,r_5s=-.2,
                                    ctx_r1_bps=.1,ctx_ask_up=.58))
        self.assertEqual(self.machine.audit()['states'][0]['status'], 'pullback_seen')

    def test_overdeep_cancels_and_does_not_rearm(self):
        self.send()
        self.assertIsNone(self.send(ctx_decision_ms=2000,ctx_price=99980.,r_5s=-2))
        self.assertEqual(self.machine.audit()['states'][0]['status'], 'cancel_overdeep')
        self.assertIsNone(self.send(ctx_decision_ms=3000))

    def test_timeout_and_tau_expiry(self):
        for changes in ({'ctx_decision_ms':11000},{'ctx_decision_ms':2000,'tau_s':119}):
            with self.subTest(changes=changes):
                self.machine=m.SignalMachine(); self.send()
                self.assertIsNone(self.send(**changes))
                self.assertEqual(self.machine.audit()['states'][0]['status'],'timeout')

    def test_epoch_and_regime_cancellation(self):
        self.send()
        self.assertIsNone(self.send(ctx_decision_ms=2000,ctx_epoch=2))
        self.assertEqual(self.machine.audit()['states'][0]['status'],'cancel_epoch_change')
        self.machine=m.SignalMachine();self.send('MAWAIT')
        self.assertIsNone(self.send('MAWAIT',ctx_decision_ms=2000,ctx_ma_direction=-1))
        self.assertEqual(self.machine.audit()['states'][0]['status'],'cancel_regime_change')

    def test_bad_quote_does_not_become_a_retry(self):
        self.send(ctx_ask_up=0)
        self.assertEqual(self.machine.audit()['states'][0]['status'],'bad_reference_quote')
        self.assertIsNone(self.send(ctx_decision_ms=2000))

    def test_states_are_separate_across_candidates_and_markets(self):
        self.send(); self.send('MAWAIT');self.send(ctx_market_start_ms=300000)
        self.assertEqual(len(self.machine.states),3)
        self.assertEqual(self.machine.audit()['event_counts']['armed'],3)

    def test_future_changes_cannot_modify_prior_actions(self):
        a=m.SignalMachine();b=m.SignalMachine();c=self.candidates['WAIT']
        prefix=[features(),features(ctx_decision_ms=2000,ctx_price=99995.,r_5s=-.2)]
        for f in prefix:
            self.assertEqual(a.signal(c,f),b.signal(c,f))
        before=deepcopy(a.audit())
        b.signal(c,features(ctx_decision_ms=3000,ctx_price=99996.,ctx_ask_up=.59))
        self.assertEqual(a.audit(),before)
        self.assertEqual(b.audit()['events'][:len(before['events'])],before['events'])

    def test_no_future_or_backward_decision_reuse(self):
        self.send()
        self.send(ctx_decision_ms=1000)
        self.assertEqual(self.machine.audit()['states'][0]['status'],'cancel_nonincreasing_decision')

    def test_specs_are_isolated_and_tampering_rejected(self):
        altered=deepcopy(self.candidates['WAIT']);altered['params']['r5_threshold_bps']=.75
        self.assertIsNone(self.machine.signal(altered,features()))
        self.assertEqual(m.catalog()[4]['params']['r5_threshold_bps'],1.125)


if __name__=='__main__':
    unittest.main(verbosity=2)
