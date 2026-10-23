"""Adversarial accounting checks, synthetic and not financial evidence."""
import copy
import unittest
from audit_cash_release_saved import reconstruct_cash_events
from cash_release_audit import reconcile


def fixture():
    row=dict(candidate_id='synthetic',latency_ms=500,attempt_id='1',market_id='fake',
      decision_ms=0,match_ms=500,status='filled_full',submitted=True,settled=True,
      limit_price=.5,fee_rate_decision=.07,reserved_usd_at_decision=51.75,
      fees_usd=1.75,filled_shares=100.,cost_usd=51.75,settlement_ms=300000,
      cash_release_due_ms=86700000,cash_released=False,payout_usd=100.,
      net_pnl_usd=48.25,cash_before_decision=1000.,cash_after_match=948.25,
      cash_after_settlement=948.25,realized_equity_after_settlement=1048.25)
    metric=dict(candidate_id='synthetic',latency_ms=500,initial_cash_usd=1000.,
      ending_cash_usd=948.25,ending_locked_payout_usd=100.,ending_reserved_usd=0.,
      realized_pnl_usd=48.25,min_available_cash_usd=948.25,fees_usd=1.75,filled_shares=100.)
    return row,metric


class ReconstructionTests(unittest.TestCase):
    def test_payout_remains_receivable_without_terminal_cash_flush(self):
        r,m=fixture();ee=reconstruct_cash_events([r],[m],86400000)
        report=reconcile([r],ee,[m],86400000)
        self.assertEqual(report['accounts'][0]['cash'],948.25)
        self.assertEqual(report['accounts'][0]['locked'],100.)
        self.assertNotIn('payout_release',[e['event_type'] for e in ee])

    def test_corrupted_intermediate_cash_is_rejected(self):
        r,m=fixture();r['cash_after_settlement']+=100
        with self.assertRaises(ValueError):reconstruct_cash_events([r],[m],86400000)

    def test_ambiguous_simultaneous_lifecycle_is_rejected(self):
        r,m=fixture();r['decision_ms']=r['match_ms']
        with self.assertRaises(ValueError):reconstruct_cash_events([r],[m],86400000)


if __name__=='__main__':unittest.main()
