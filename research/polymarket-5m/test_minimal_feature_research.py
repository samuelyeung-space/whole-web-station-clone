import unittest
from minimal_feature_research import vector,execution_inputs,eligible_training,DAY
from minimal_feature_strategy import candidates,signal


def decision(t=1000):
    return {'market_id':'A','recv_ms':t,'features':{'mid_up':.5,'tau_s':200},
            'feature_available_ms':{'mid_up':t,'tau_s':t}}


def book(t=1000):
    return {'recv_ms':t,'up_recv_ms':t,'down_recv_ms':t,'healthy':True,'up_healthy':True,'fee_rate':.07,
        'up_asks':[[.51,100]],'up_bids':[[.49,100]],'down_asks':[[.51,100]]}


class MinimalTests(unittest.TestCase):
    def test_time_does_not_need_returns(self):
        v=vector(decision(),book())
        self.assertIn('time',v['vectors']);self.assertNotIn('trend',v['vectors'])
        self.assertEqual(len(v['vectors']['time']),6)

    def test_trend_requires_all_four_returns_and_availability(self):
        e=decision()
        for n in ['ep_r_5s','ep_r_15s','ep_r_30s','ep_r_60s']:
            e['features'][n]=1.;e['feature_available_ms'][n]=1000
        self.assertEqual(len(vector(e,book())['vectors']['trend']),12)
        e['feature_available_ms']['ep_r_60s']=1001
        self.assertNotIn('trend',vector(e,book())['vectors'])

    def test_down_not_needed_for_time_but_required_for_execution(self):
        b=book();b['healthy']=False;b['down_asks']=[];b['down_recv_ms']=None
        self.assertIsNotNone(vector(decision(),b))
        self.assertEqual(execution_inputs(decision(),b),{})

    def test_no_future_or_stale_up(self):
        for value in [1001,749]:
            b=book();b['up_recv_ms']=value
            self.assertIsNone(vector(decision(),b))

    def test_mid_book_mismatch_rejected(self):
        e=decision();e['features']['mid_up']=.6
        self.assertIsNone(vector(e,book()))

    def test_training_excludes_same_day_and_future_label(self):
        rows=[{'market':'A','t':1000,'day':0,'vectors':{'time':[1]}},
              {'market':'B','t':2000,'day':0,'vectors':{'time':[1]}},
              {'market':'C','t':DAY+1000,'day':1,'vectors':{'time':[1]}}]
        markets={k:{'end_ms':3000} for k in 'ABC'}
        labels={'A':(1,4000),'B':(0,DAY+1),'C':(1,5000)}
        self.assertEqual([r['market'] for r in eligible_training(rows,labels,markets,1,'time','expanded',set())],['A'])

    def test_fixed_training_never_expands(self):
        row={'market':'A','t':1000,'day':0,'vectors':{'time':[1]}}
        self.assertEqual(eligible_training([row],{'A':(1,4000)},{'A':{'end_ms':3000}},1,'time','fixed',set()),[])

    def test_execution_fields_are_separate_from_old_frozen_fields(self):
        extra=execution_inputs(decision(),book());self.assertEqual(set(extra),{'mfa_ask_up','mfa_ask_down','mfa_fee'})

    def test_eight_trials_no_price_chasing_or_derivative_required(self):
        cs=candidates();self.assertEqual(len(cs),8);self.assertEqual(len({c['id'] for c in cs}),8)
        f={'tau_s':200,'mfa_ask_up':.5,'mfa_ask_down':.51,'mfa_fee':.07}
        for c in cs:
            f[c['score_feature']]=.7
            self.assertEqual(signal(c,f),'Up')
        f['tau_s']=10
        self.assertIsNone(signal(cs[0],f))


if __name__=='__main__':unittest.main()
