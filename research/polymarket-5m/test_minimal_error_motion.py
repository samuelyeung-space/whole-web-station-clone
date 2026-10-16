import copy,unittest
from minimal_error_motion_features import Enricher
from minimal_error_motion_strategy import candidates,signal

def row(t,q=.7,mid='a'):
    f={'mfa_trend_fixed':q,'mfa_trend_expanded':q,'mid_up':.5,'tau_s':200}
    return {'type':'decision','recv_ms':t,'market_id':mid,'features':f,'feature_available_ms':dict.fromkeys(f,t)}

class Tests(unittest.TestCase):
    def test_catalog(self):
        cs=candidates();self.assertEqual(len(cs),18);self.assertEqual(len({c['id'] for c in cs}),18)
        self.assertEqual(sum(not c.get('control',False) for c in cs),8)
    def test_repeatable_strategy_cli(self):
        from run_minimal_error_motion import strategy_args
        self.assertEqual(strategy_args(['m:a','m:b']),['--strategy','m:a','--strategy','m:b'])
    def test_no_signal_is_not_zero_pnl(self):
        from verify_minimal_error_controls import contrast
        self.assertIsNone(contrast(None,-10));self.assertIsNone(contrast(5,None))
        self.assertEqual(contrast(0,-10),10)
    def test_prefix(self):
        def run(n):
            en=Enricher();return [en.process(row(i*1000,.6+i*.001)) for i in range(n)]
        self.assertEqual(run(35),run(70)[:35])
    def test_warmup_gap(self):
        en=Enricher()
        for i in range(19):self.assertNotIn('mem_fixed_5_v',en.process(row(i*1000))['features'])
        self.assertIn('mem_fixed_5_v',en.process(row(19000))['features'])
        self.assertNotIn('mem_fixed_5_v',en.process(row(21000))['features'])
    def test_missing_one_model_does_not_reset_other(self):
        en=Enricher()
        for i in range(25):en.process(row(i*1000))
        e=row(25000);del e['features']['mfa_trend_fixed'];f=en.process(e)['features']
        self.assertNotIn('mem_fixed_5_v',f);self.assertIn('mem_expanded_5_v',f)
    def test_future(self):
        en=Enricher();e=row(0);e['feature_available_ms']['mfa_trend_fixed']=1
        self.assertNotIn('mem_fixed_5_v',en.process(e)['features'])
    def test_control_same_admission(self):
        c=candidates()[0];control=candidates()[8]
        self.assertEqual(c['required_features'],control['required_features'])
        f={'mfa_trend_fixed':.8,'mfa_ask_up':.5,'mfa_ask_down':.6,'mfa_fee':.07,'tau_s':200,
            'ep_r_5s':1,'mem_fixed_5_e':.1,'mem_fixed_5_v':.1,'mem_fixed_5_dq':.01}
        self.assertIsNone(signal(c,f));self.assertEqual(signal(control,f),'Up')
    def test_unconditional_control_parity(self):
        import minimal_feature_strategy as mfa
        for c,old in zip(candidates()[-2:],(mfa.candidates()[5],mfa.candidates()[7])):
            for q in (.01,.4,.7,.99):
                f={'tau_s':100,c['score_feature']:q,'mfa_ask_up':.5,'mfa_ask_down':.6,'mfa_fee':.07}
                self.assertEqual(signal(c,f),mfa.signal(old,f))
    def test_no_label_access(self):
        e={'type':'outcome','winner':'Up'};self.assertEqual(Enricher().process(copy.deepcopy(e)),e)
    def test_exact_frozen_eco_plain_math(self):
        from error_motion_features import Enricher as Old
        new,old=Enricher(),Old()
        for i in range(100):
            e=row(i*1000+1000*(i>=70),.6+.001*i)
            f=e['features'];reference=copy.deepcopy(e)
            reference['features']['book_5']=f['mfa_trend_fixed']
            reference['feature_available_ms']['book_5']=e['recv_ms']
            a=new.process(e)['features'];b=old.process(reference)['features']
            for h in (5,15):
                for k in ('e','v','a','j','dq'):
                    self.assertEqual(a.get(f'mem_fixed_{h}_{k}'),b.get(f'eco_plain_{h}_{k}'))

if __name__=='__main__':unittest.main()
