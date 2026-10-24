import unittest
from source_race_quantity_audit import audit_rows


def row(**kw):
    value=dict(sent=True,target_shares=5,signed_size=5,maker_usd=2.5,
        fixed_limit=0.5,filled_shares=5,filled=True,fill_price=0.5,
        evaluation_ms=500,pnl=0)
    value.update(kw)
    return value


class QuantityTests(unittest.TestCase):
    def test_exact_target_is_share_capped(self):
        self.assertTrue(audit_rows([row()])['share_capped_quantity_admission'])
    def test_minimum_budget_uplift_is_not_share_capped(self):
        a=audit_rows([row(signed_size=10,maker_usd=1,fixed_limit=.1,
            filled_shares=10,fill_price=.1)])
        self.assertFalse(a['share_capped_quantity_admission'])
        self.assertEqual(a['by_latency']['500']['signed_size_above_target'],1)
    def test_price_improvement_can_buy_more_than_signed_size(self):
        a=audit_rows([row(filled_shares=10,fill_price=.25)])
        self.assertFalse(a['share_capped_quantity_admission'])
        self.assertEqual(a['by_latency']['500']['fills_above_signed_size'],1)
        self.assertIsNone(a['corrected_share_capped_pnl_usd'])
    def test_budget_violation_rejected(self):
        with self.assertRaises(ValueError):audit_rows([row(filled_shares=6)])
    def test_empty_not_admitted(self):
        self.assertFalse(audit_rows([])['share_capped_quantity_admission'])


if __name__=='__main__':unittest.main()
