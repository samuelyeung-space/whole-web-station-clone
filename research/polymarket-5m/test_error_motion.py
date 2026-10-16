import copy,unittest
from error_motion_features import Enricher,State,variance
from error_motion_strategy import candidates,signal

def row(t,q=.7,p=.5,mid='x',tau=200):
    return {'type':'decision','market_id':mid,'recv_ms':t,'features':{'book_5':q,'mid_up':p,'tau_s':tau},
            'feature_available_ms':{'book_5':t,'mid_up':t,'tau_s':t}}

class Tests(unittest.TestCase):
    def test_registered_count(self):
        cs=candidates();self.assertEqual(len(cs),18);self.assertEqual(len(set(c['id'] for c in cs)),18)
    def test_warmup_and_gap_reset(self):
        s=State(5)
        for t in range(19):self.assertFalse(s.update(t*1000,1,.7))
        self.assertIn('v',s.update(19000,1,.7));self.assertFalse(s.update(21000,1,.7))
    def test_prefix_invariance(self):
        def run(rows):
            e=Enricher();return [e.process(copy.deepcopy(r)) for r in rows]
        a=[row(i*1000,q=.6+i*.001) for i in range(80)]
        self.assertEqual(run(a[:40]),run(a)[:40])
    def test_future_feature_rejected(self):
        e=Enricher();r=row(0);r['feature_available_ms']['book_5']=1
        self.assertEqual(e.process(r)['features'],{'book_5':.7,'mid_up':.5,'tau_s':200})
    def test_market_states_separate(self):
        e=Enricher()
        for i in range(25):e.process(row(i*1000))
        r=e.process(row(25000,mid='new'));self.assertNotIn('eco_plain_5_v',r['features'])
    def test_no_outcome_access(self):
        e=Enricher();r={'type':'outcome','future':'label'};self.assertEqual(e.process(r),r)
    def test_time_variance_boundary_reset(self):
        self.assertAlmostEqual(variance(60),20)
        e=Enricher()
        for i in range(70):e.process(row(i*1000,tau=130-i))
        r=e.process(row(70000,tau=59));self.assertIn('eco_plain_5_v',r['features'])
        self.assertNotIn('eco_twap_variance_5_v',r['features'])
    def test_fees_and_error_gate(self):
        c=candidates()[0];p='eco_plain_5'
        f={'book_5':.8,'ask_up':.6,'ask_down':.5,'fee':.07,'tau_s':100,'r5':2,'imbalance':.4,
           p+'_e':.5,p+'_v':-.1,p+'_a':0,p+'_j':0,p+'_dq':.01}
        self.assertEqual(signal(c,f),'Up');f[p+'_v']=.1;self.assertIsNone(signal(c,f))

if __name__=='__main__':unittest.main()
