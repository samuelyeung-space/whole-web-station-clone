"""Integration checks use a synthetic legacy admission fixture, not price data."""
import unittest
from pathlib import Path
import test_seven_day_ranking as legacy
import rank7_cash_admission as guard
from seven_day_ranking import sha


class CashAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.base=legacy.SevenDayRankingTests();self.base.setUp()
        # Populate all attempt/summary fields so the negative probe isolates
        # missing cash transitions rather than an unrelated schema omission.
        for result in self.base.results.values():
            for i,row in enumerate(result['attempts'],1):
                row.update(attempt_id=str(i),requested_shares=100,fill_vwap=.4,
                    limit_price=.4,side='Up',winner='Up',
                    reserved_usd_at_decision=row['cost_usd'],cash_before_decision=1000,
                    cash_after_match=1000-row['cost_usd'],cash_release_due_ms=row['settlement_ms'],
                    cash_released=True,cash_after_settlement=1000+row['net_pnl_usd'])
            for row in result['summary']:
                row.update(ending_cash_usd=1000+row['net_pnl_usd'],ending_reserved_usd=0,
                    ending_locked_payout_usd=0,min_available_cash_usd=1000-(100-row['net_pnl_usd']),
                    max_realized_drawdown_usd=0)
        self.base.build()
        self.protocol=dict(schema='rank7-cash-protocol-v1',registered_at_utc='1970-01-08T00:00:00Z',
            cash_admission_code_sha256={n:sha(Path(__file__).with_name(n)) for n in
                ('rank7_cash_admission.py','rank7_cash_integrity.py')},rank7_protocol=self.base.protocol)
    def tearDown(self):self.base.tearDown()
    def check(self):return guard.assess(self.protocol,self.base.root,self.base.cache)

    def test_legacy_fixture_passes_old_gate_but_empty_cash_blocks_guard(self):
        self.assertEqual(self.base.assess()['status'],'assessed_development_only')
        r=self.check();self.assertEqual(r['status'],'blocked');self.assertEqual(r['ranking'],[])
        self.assertIsNone(r['net_pnl_usd']);self.assertIsNone(r['formal_oos_result'])
        self.assertIn('missing mandatory order cash transitions',r['reasons'][0])

    def complete_engine_fixture(self):
        import replay_engine as engine
        from test_rank7_cash_integrity import signal
        # These are synthetic input prices; replay creates the ledger rather
        # than this test copying the guard's accounting implementation.
        for event in self.base.events:
            if event['type']=='market':
                event.update(min_order_size=5,up_tick_size=.01,down_tick_size=.01,constraints_source='gamma')
            if event['type']=='decision' and event['market_id']=='BTC-0':
                event.update(features={'x':1,'tau_s':299.9},feature_available_ms={'x':100,'tau_s':100})
        for at in (600,700,1100):
            self.base.events.append(dict(type='book',recv_ms=at,market_id='BTC-0',healthy=True,
                fee_rate=.07,up_asks=[[.4,100]],down_asks=[[.6,100]]))
        self.base.bind_input()
        for scenario in self.base.scenarios:
            r=engine.replay_events(sorted(self.base.events,key=lambda x:x['recv_ms']),
                self.base.registration['strategies']['candidate_rules'],signal,
                engine.ReplayConfig(**scenario['config']))
            r.update(mode='isolated',engine_config=scenario['config'],inherited_execution_assumptions=[])
            self.base.results[scenario['name']]=r
        self.base.build()

    def test_actual_engine_output_can_pass_guard_with_complete_cash_trace(self):
        self.complete_engine_fixture();result=self.check()
        self.assertEqual(result['status'],'assessed_development_only',result)
        self.assertEqual(len(result['ranking']),1)
        self.assertEqual(len(result['cash_evidence']),3)
        self.assertEqual(result['qualified_strategies'],0)

    def mutate_summary_sidecars(self,field,value):
        import json
        root=self.base.root;name=self.base.scenarios[0]['name']
        audit=json.loads((root/name/'audit.json').read_text())
        audit['summary'][0][field]=value
        self.base.write(root/name/'audit.json',audit)
        metrics=json.loads((root/'metrics.json').read_text())
        for row in metrics:
            if row['scenario']==name and row['latency_ms']==500:row[field]=value
        self.base.write(root/'metrics.json',metrics)
        done=json.loads((root/'completion.json').read_text())
        for rel in [name+'/audit.json','metrics.json']:
            done['output_sha256'][rel]=sha(root/rel)
        self.base.write(root/'completion.json',done)

    def test_stale_summary_hash_refresh_cannot_inflate_ranking_profit(self):
        self.complete_engine_fixture();self.mutate_summary_sidecars('net_pnl_usd',558.32)
        self.assertEqual(self.base.assess()['status'],'assessed_development_only')
        r=self.check();self.assertEqual(r['status'],'blocked')
        self.assertIn('summary/cash mismatch: net_pnl_usd',r['reasons'][0])
        self.assertEqual(r['ranking'],[]);self.assertIsNone(r['net_pnl_usd'])

    def test_stale_filled_quantity_cannot_change_capacity_or_share_returns(self):
        self.complete_engine_fixture();self.mutate_summary_sidecars('filled_shares',1)
        self.assertEqual(self.base.assess()['status'],'assessed_development_only')
        r=self.check();self.assertEqual(r['status'],'blocked')
        self.assertIn('summary/attempt mismatch: filled_shares',r['reasons'][0])
        self.assertEqual(r['ranking'],[])

    def test_late_guard_cannot_upgrade_old_replay(self):
        self.protocol['registered_at_utc']='1970-01-10T00:00:00Z'
        self.assertIn('after replay',self.check()['reasons'][0])

    def test_code_identity_and_null_protocol_fail_closed(self):
        self.protocol['cash_admission_code_sha256']['rank7_cash_integrity.py']='0'*64
        self.assertIn('code identity',self.check()['reasons'][0])
        self.assertEqual(guard.assess({},self.base.root,self.base.cache)['ranking'],[])


if __name__=='__main__':unittest.main()
