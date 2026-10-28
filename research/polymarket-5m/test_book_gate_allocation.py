import unittest
from book_gate_allocation import route,legs

class RoutingTests(unittest.TestCase):
    def test_gate_dispatch_does_not_depend_on_price_discount(self):
        c=[dict(market_id='m',decision_ms=0)]
        rows=[dict(type='book',market_id='m',recv_ms=10,up_asks=[[.8,100]]),
            dict(type='decision',market_id='m',recv_ms=10,features=dict(experiment_side=1,experiment_kind=1,tau_s=20))]
        out=[r for r in route(rows,c) if r['type']=='decision']
        self.assertEqual(len(out),4)
        self.assertTrue(all(r['features']['patient_limit']==.8 for r in out))
        self.assertTrue(all(r['feature_available_ms']['patient_limit']==10 for r in out))
        self.assertFalse(any('NOW100' in r['patient_dispatch_id'] for r in out))
    def test_legs_share_policy_ledger_and_keep_exact_quantities(self):
        pp=[r for r in legs() if r['_policy']=='BASE25_GATE75']
        self.assertEqual([r['_patient_quantity'] for r in pp],[25,75])
        self.assertEqual(pp[0]['_ledger_id'],pp[1]['_ledger_id'])
        self.assertEqual([r['_phase'] for r in pp],[0,1])

if __name__=='__main__':unittest.main()
