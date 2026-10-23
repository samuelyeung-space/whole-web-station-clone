"""Cash rejection counterexamples only; no financial performance evidence."""
import unittest
from audit_pair_portfolio import audit_decision_cash


def attempt(cash):
    return dict(candidate_id='synthetic',latency_ms=500,attempt_id='current',
      decision_ms=2,submitted=False,status='rejected_insufficient_available_cash',
      cash_before_decision=cash,limit_price=.5,fee_rate_decision=.07,
      reserved_usd_at_decision=0.,cash_after_match=None,settled=False)


class DecisionCashTests(unittest.TestCase):
    def test_unfunded_order_rejection_has_a_provable_cash_shortfall(self):
        e=dict(candidate_id='synthetic',latency_ms=500,attempt_id='previous',
               event_ms=1,event_type='fill',cash_usd=10.,reserved_usd=0.)
        a=audit_decision_cash([attempt(10.)],[e],{'extra_cost_per_filled_share_usd':0.})
        self.assertEqual(a['cash_rejections_proved_insufficient'],1)

    def test_cash_rejection_with_enough_cash_is_refused(self):
        with self.assertRaises(ValueError):
            audit_decision_cash([attempt(1000.)],[],{'extra_cost_per_filled_share_usd':0.})

    def test_saved_decision_cash_cannot_be_fabricated(self):
        r=attempt(1100.);r['status']='rejected_unhealthy_book_at_decision'
        with self.assertRaises(ValueError):
            audit_decision_cash([r],[],{'extra_cost_per_filled_share_usd':0.})


if __name__=='__main__':unittest.main()
