"""Synthetic integration checks, not historical candidate backtests."""
import unittest
from recorder_futures_flow import RecorderFuturesFlowTape, CONTRACT, EXCHANGE, INSTRUMENT
from historical_flow_receipts import CoverageInterval
import usdm_flow_execution_bridge as b


def fixture(coverage=True):
    tape=RecorderFuturesFlowTape(capture_run_id='synthetic',recorder_contract=CONTRACT,time_unit='MILLISECOND',split='development')
    for i,t in enumerate([230000,279000]):
        tape.add_trade(dict(kind='trade',s='BTCUSDT',recv_ms=t,T=t,E=t,t=i+1,p='100',q='1',m=False,connection_epoch=1))
    if coverage:tape.add_coverage(CoverageInterval(EXCHANGE,INSTRUMENT,'synthetic',0,280000,280000,'complete','synthetic coverage only'))
    events=[dict(type='dataset',recv_ms=0,dataset_id='synthetic_fixture',split='development',coverage_end_ms=282000,provenance={'source':'synthetic only'}),
        dict(type='market',market_id='m',recv_ms=0,start_ms=0,end_ms=300000,label_source='gamma',min_order_size=5.,up_tick_size=.01,down_tick_size=.01,constraints_source='clob'),
        dict(type='book',market_id='m',recv_ms=280000,up_asks=[[.5,100]],down_asks=[[.5,100]],healthy=True,fee_rate=.07),
        dict(type='decision',market_id='m',recv_ms=280000,features={},feature_available_ms={})]
    for t in [280500,280600,281000]:events.append(dict(type='book',market_id='m',recv_ms=t,up_asks=[[.49,40]],down_asks=[[.5,100]],healthy=True,fee_rate=.07))
    events.append(dict(type='outcome',market_id='m',recv_ms=300001,resolved_ms=300000,winner='Up',source='gamma'))
    bindings=[dict(market_id='m',decision_recv_ms=280000,tape=tape,capture_clock_id='synthetic:recv_ms',tau_evidence=dict(value=20,as_of_ms=280000,available_ms=280000,capture_clock_id='synthetic:recv_ms',provenance='synthetic tau'))]
    return events,bindings


class Integration(unittest.TestCase):
    def run_fixture(self,events,bindings):return b.replay_development(events,bindings,split='development',input_evidence_class='synthetic_fixture')
    def test_real_engine_fixed_limit_partial_fak_and_all_stresses(self):
        r=self.run_fixture(*fixture());self.assertEqual(set(r['scenarios']),{'base','half_depth','extra_1cent'})
        for name,result in r['scenarios'].items():
            self.assertEqual(len(result['summary']),12)
            for a in result['attempts']:
                self.assertEqual(a['requested_shares'],100)
                self.assertEqual(a['match_ms']-a['decision_ms'],a['latency_ms'])
                self.assertEqual(a['limit_price'],.5)
                self.assertEqual(a['filled_shares'],20 if name=='half_depth' else 40)
                self.assertGreater(a['fees_usd'],0)
                self.assertTrue(a['settled'])
            self.assertEqual(result['config']['initial_cash_usd'],1000)
        self.assertFalse(r['main_ranking_admitted']);self.assertIsNone(r['formal_oos_result'])
    def test_missing_coverage_does_not_become_zero_flow(self):
        r=self.run_fixture(*fixture(False));self.assertTrue(all(not x['attempts'] for x in r['scenarios'].values()))
        self.assertTrue(all(x['status']=='blocked_features' for x in r['decision_binding_audit'][0]['result']['rows']))
    def test_missing_binding_is_retained_blocker(self):
        e,_=fixture();r=self.run_fixture(e,[]);self.assertEqual(r['decision_binding_audit'][0]['status'],'missing_binding');self.assertTrue(all(not x['attempts'] for x in r['scenarios'].values()))
    def test_future_tau_blocked(self):
        e,x=fixture();x[0]['tau_evidence']['available_ms']=280001;r=self.run_fixture(e,x);self.assertTrue(all(not q['attempts'] for q in r['scenarios'].values()))
    def test_scalar_override_and_duplicate_binding_rejected(self):
        e,x=fixture();e[3]['features']['flow_15s']=1
        with self.assertRaises(ValueError):self.run_fixture(e,x)
        e,x=fixture()
        with self.assertRaises(ValueError):self.run_fixture(e,x+x)
    def test_oos_and_wrong_clock_rejected(self):
        e,x=fixture()
        with self.assertRaises(ValueError):b.replay_development(e,x,split='OOS',input_evidence_class='consumed_development')
        x[0]['capture_clock_id']='other'
        with self.assertRaises(ValueError):self.run_fixture(e,x)

if __name__=='__main__':unittest.main()
