import copy
import tempfile
from pathlib import Path
import unittest
import common_friction_panel as p


class CommonFrictionTests(unittest.TestCase):
    def test_catalog_and_selection_fixed(self):
        rules=p.candidates();self.assertEqual(len(rules),35)
        self.assertEqual([c['id'] for c in rules[:3]],list(p.LEGACY_IDS))
        self.assertEqual(sum(c['id'].startswith('DPP-') for c in rules),24)
        self.assertEqual(sum(c['id'].startswith('MFA-') for c in rules),8)
        self.assertEqual(len({c['id'] for c in rules}),35)

    def test_prespecified_pressure_not_size_rescaling(self):
        configs=dict(p.configurations());self.assertEqual(len(configs),3)
        for cfg in configs.values():
            self.assertEqual((cfg.initial_cash_usd,cfg.shares,cfg.latencies_ms),(1000,100,(500,600,1000)))
            self.assertTrue(cfg.require_constraints);self.assertFalse(cfg.full_or_zero)
        self.assertEqual(configs['stack_1h_reject10'].cash_release_delay_ms,3600000)
        self.assertEqual(configs['stack_4h_reject30'].post_send_reject_bps,3000)
        self.assertEqual(configs['stack_4h_reject30'].depth_fraction,.5)
        self.assertEqual(configs['stack_4h_reject30'].extra_cost_per_filled_share_usd,.01)

    def test_first_intents_excludes_outcome_and_cash(self):
        a=dict(candidate_id='X',latency_ms=500,market_id='m',decision_ms=1,match_ms=501,
               side='Up',limit_price=.5,net_pnl_usd=100)
        b={**a,'net_pnl_usd':-100,'status':'rejected_insufficient_available_cash'}
        self.assertEqual(p.first_intents([a]),p.first_intents([b]))
        b['limit_price']=.51;self.assertNotEqual(p.first_intents([a]),p.first_intents([b]))

    def test_mfa_parity_refuses_missing_cells(self):
        with self.assertRaisesRegex(ValueError,'cell mismatch'):p.check_mfa_parity([],[],[],[])

    def parity_fixture(self):
        fields=('status','attempts','submitted_orders','full_fills','partial_fills','filled_shares',
          'settled_positions','unresolved_positions','net_pnl_usd','fees_usd','ending_cash_usd',
          'max_realized_drawdown_usd','min_available_cash_usd','ending_reserved_usd','ending_locked_payout_usd')
        old=[];orders=[]
        for i in range(1,9):
            for latency in (500,600,1000):
                r=dict.fromkeys(fields,0);r.update(candidate_id=f'MFA-{i:03}',latency_ms=latency,
                    status='simulated_complete',scenario='shares100_base_release0',filled_shares=100.0)
                old.append(r);orders.append(dict(candidate_id=r['candidate_id'],latency_ms=latency,
                    market_id='m',attempt_id=str(len(orders)),filled_shares=100.0,net_pnl_usd=5.0))
        return old,copy.deepcopy(old),orders,copy.deepcopy(orders)

    def test_share_roundoff_only_after_exact_order_parity(self):
        old,new,oa,na=self.parity_fixture();new[0]['filled_shares']+=1e-12
        na[0]['attempt_id']='renumbered';na[0]['filled_shares']=100
        result=p.check_mfa_parity(old,new,oa,na)
        self.assertEqual(result['compared_orders'],24)
        self.assertEqual(len(result['aggregate_share_roundoff']),1)
        na[0]['filled_shares']=99.999999999999
        with self.assertRaisesRegex(ValueError,'per-order'):p.check_mfa_parity(old,new,oa,na)

    def test_monetary_or_material_share_mismatch_not_tolerated(self):
        for field,delta in (('net_pnl_usd',1e-12),('ending_cash_usd',1e-12),('filled_shares',1e-6)):
            old,new,oa,na=self.parity_fixture();new[0][field]+=delta
            with self.assertRaisesRegex(ValueError,'financial/count'):p.check_mfa_parity(old,new,oa,na)

    def test_dispatch_fails_closed_for_unregistered(self):
        with self.assertRaisesRegex(ValueError,'unregistered'):p.signal({'id':'OTHER'}, {})

    def test_ledger_roundtrip_and_crc(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.gz';receipt=p.write_rows(path,[{'x':1},{'x':2}])
            self.assertEqual(receipt['rows'],2);self.assertTrue(receipt['gzip_eof_crc_verified'])
            self.assertEqual(list(p.rows(path)),[{'x':1},{'x':2}])
            with self.assertRaises(FileExistsError):p.write_rows(path,[])


if __name__=='__main__':unittest.main()
