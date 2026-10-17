import copy
import unittest
from cash_release_audit import reconcile


class CashReleaseAuditTest(unittest.TestCase):
    def fixture(self, released=True):
        r=dict(attempt_id='1',candidate_id='X',latency_ms=500,submitted=True,
            decision_ms=0,match_ms=500,settlement_ms=1000,cash_release_due_ms=2000,
            reserved_usd_at_decision=51.,cost_usd=50.,filled_shares=100.,fees_usd=1.,
            settled=True,payout_usd=100.,net_pnl_usd=50.,cash_released=released,status='filled_full')
        values=[('reservation',0,1000.,51.,0.,0.),('reservation_release',500,1000.,0.,0.,0.),
                ('fill',500,950.,0.,0.,0.),('settlement',1000,950.,0.,100.,50.)]
        if released:values.append(('payout_release',2000,1050.,0.,0.,50.))
        events=[dict(event_sequence=i+1,event_type=k,event_ms=t,cash_usd=c,reserved_usd=z,
            locked_payout_usd=l,realized_pnl_usd=p,candidate_id='X',latency_ms=500,attempt_id='1')
            for i,(k,t,c,z,l,p) in enumerate(values)]
        m=dict(candidate_id='X',latency_ms=500,initial_cash_usd=1000.,ending_cash_usd=1050. if released else 950.,
            ending_locked_payout_usd=0. if released else 100.,ending_reserved_usd=0.,
            realized_pnl_usd=50.,min_available_cash_usd=949.,fees_usd=1.,filled_shares=100.)
        return [r],events,[m]

    def test_completed_release(self):
        self.assertEqual(reconcile(*self.fixture(),1000)['status'],'passed')

    def test_pending_not_forced_into_cash(self):
        self.assertEqual(reconcile(*self.fixture(False),1000)['accounts'][0]['locked'],100.)

    def test_cash_tamper_fails(self):
        rows,events,metrics=self.fixture();events[3]['cash_usd']+=100
        with self.assertRaises(ValueError):reconcile(rows,events,metrics,1000)

    def test_early_release_fails(self):
        rows,events,metrics=self.fixture();events[-1]['event_ms']=1999
        with self.assertRaises(ValueError):reconcile(rows,events,metrics,1000)

    def test_missing_release_fails(self):
        rows,events,metrics=self.fixture()
        with self.assertRaises(ValueError):reconcile(rows,events[:-1],metrics,1000)

    def test_duplicate_event_fails(self):
        rows,events,metrics=self.fixture();duplicate=copy.deepcopy(events[-1]);duplicate['event_sequence']=6
        with self.assertRaises(ValueError):reconcile(rows,events+[duplicate],metrics,1000)


if __name__=='__main__':unittest.main()
