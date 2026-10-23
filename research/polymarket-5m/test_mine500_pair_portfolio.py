"""Synthetic arbitration counterexamples, never profitable evidence."""
import unittest
from mine500_pair_portfolio import arbitrate


def tape(future=False):
    market=dict(type='market',recv_ms=0,market_id='synthetic',start_ms=-99000,end_ms=201000)
    f=dict(tau_s=200.,r_5s=2.,pm_r_5s=.05,pm_r_15s=.04)
    a={k:1000 for k in f}
    if future:a['r_5s']=1001
    decision=dict(type='decision',recv_ms=1000,market_id='synthetic',features=f,feature_available_ms=a)
    later=dict(type='decision',recv_ms=2000,market_id='synthetic',
      features={**f,'tau_s':199.,'r_5s':-2.},feature_available_ms={k:2000 for k in f})
    return [market,decision,later]


class ArbitrationTests(unittest.TestCase):
    def test_opposite_simultaneous_signals_use_fixed_id_priority(self):
        events,diagnostic=arbitrate(tape())
        self.assertEqual(diagnostic['choices'][0]['selected_candidate'],'M500-010')
        self.assertEqual(diagnostic['choices'][0]['side'],'Up')
        self.assertTrue(diagnostic['choices'][0]['opposite_simultaneous'])

    def test_future_component_feature_cannot_win_arbitration(self):
        events,diagnostic=arbitrate(tape(future=True))
        self.assertEqual(diagnostic['choices'][0]['selected_candidate'],'M500-680')
        self.assertEqual(diagnostic['choices'][0]['side'],'Down')

    def test_later_signal_cannot_retry_failed_first_order(self):
        events,diagnostic=arbitrate(tape())
        self.assertEqual(len(diagnostic['choices']),1)
        self.assertEqual(events[-1]['features']['portfolio_side'],0)

    def test_future_health_and_labels_do_not_change_first_selection(self):
        source=tape();expected=arbitrate(source)[1]['choices']
        source[-1]['future_healthy']=False;source[-1]['winner']='Down'
        self.assertEqual(arbitrate(source)[1]['choices'],expected)

    def test_invalid_market_tau_does_not_create_signal(self):
        source=tape()[:2];source[-1]['features']['tau_s']=201
        self.assertEqual(arbitrate(source)[1]['choices'],[])


if __name__=='__main__':unittest.main()
