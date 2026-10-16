import copy
import unittest
from unittest.mock import patch
import joint_probability_execution as ex
import joint_portfolio_research as p


def tape(two=True):
    rows=[{'type':'dataset','recv_ms':0,'dataset_id':'synthetic-test-only','split':'development','provenance':{'source':'test'}}]
    for k,start in [('m',0),('n',300000)][:2 if two else 1]:
        rows.append({'type':'market','recv_ms':start,'market_id':k,'start_ms':start,'end_ms':start+300000,'label_source':'gamma'})
        for offset in [1000,1500]:
            rows.append({'type':'book','recv_ms':start+offset,'market_id':k,'healthy':True,'fee_rate':0,'up_asks':[[.5,100]],'down_asks':[[.5,100]]})
        rows.append({'type':'decision','recv_ms':start+1000,'market_id':k,'features':{'tau_s':299,'portfolio_side':1},'feature_available_ms':{'tau_s':start+1000,'portfolio_side':start+1000}})
        rows.append({'type':'outcome','recv_ms':start+300000,'resolved_ms':start+300000,'market_id':k,'source':'gamma','winner':'Up'})
    return sorted(rows,key=lambda r:r['recv_ms'])


class Tests(unittest.TestCase):
    def replay(self,delay=0,two=True):
        return ex.replay_events(tape(two),[{'id':'P','required_features':['portfolio_side']}],p.portfolio_signal,
            ex.ReplayConfig(shares=100,initial_cash_usd=60,latencies_ms=(500,),cash_release_delay_ms=delay))

    def test_delay_prevents_reuse_without_delaying_settlement(self):
        a=self.replay();b=self.replay(60000)
        self.assertEqual(a['summary'][0]['full_fills'],2)
        self.assertEqual(b['attempts'][1]['status'],'rejected_insufficient_available_cash')
        self.assertEqual(b['attempts'][0]['settlement_ms'],300000)
        self.assertEqual(b['attempts'][0]['cash_release_due_ms'],360000)
        self.assertEqual(b['summary'][0]['ending_cash_usd'],110)
        self.assertEqual(b['summary'][0]['ending_locked_payout_usd'],0)

    def test_terminal_receivable_not_fabricated_cash(self):
        r=self.replay(60000,False)['summary'][0]
        self.assertEqual(r['ending_cash_usd'],10)
        self.assertEqual(r['ending_locked_payout_usd'],100)
        self.assertEqual(r['net_pnl_usd'],50)

    def test_union_validation_and_first_signal(self):
        rules=[({'id':'A','required_features':['a']},lambda c,f:'Up'),
               ({'id':'B','required_features':['b']},lambda c,f:'Down')]
        row={'type':'decision','market_id':'m','recv_ms':1,'features':{'a':1,'b':1,'tau_s':200},
             'feature_available_ms':{'a':2,'b':1,'tau_s':1}}
        later=copy.deepcopy(row);later['recv_ms']=2
        with patch.object(p,'selected_rules',lambda:rules):
            ev,choices,first,invalid=p.arbitrate([row,later])
        self.assertEqual(choices[0]['selected_candidate'],'B')
        self.assertEqual(len(choices),1)
        self.assertEqual(ev[1]['features']['portfolio_side'],0)

    def test_priority_does_not_inspect_future(self):
        rules=[({'id':'A','required_features':['a']},lambda c,f:'Up'),
               ({'id':'B','required_features':['b']},lambda c,f:'Down')]
        row={'type':'decision','market_id':'m','recv_ms':1,'features':{'a':1,'b':1,'tau_s':200},
             'feature_available_ms':{'a':1,'b':1,'tau_s':1}}
        with patch.object(p,'selected_rules',lambda:rules):
            _,choices,_,_=p.arbitrate([row])
        self.assertEqual(choices[0]['selected_candidate'],'A')
        self.assertTrue(choices[0]['opposite_simultaneous'])


if __name__=='__main__':unittest.main()
