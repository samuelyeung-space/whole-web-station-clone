import unittest
from parent_book_allocations import inputs_for,route

class DispatchTests(unittest.TestCase):
    def fixture(self):
        c={'unique_arms':{'a':dict(market_id='m',decision_ms=0,side='Down')}}
        p={(kind,'a'):{h:dict(request_id=f'{kind}:a:{h}',at_ms=h+(100 if kind!='parent' else 0)) for h in [0,500,600,1000]} for kind in ['parent','balance','vacuum']}
        g={(kind,'a'):dict(parentkey='m',recv_ms=100,status='balance_ready' if kind=='balance' else 'vacuum_repair_ready') for kind in ['balance','vacuum']}
        return c,p,g
    def test_overlapping_families_remain_separate(self):
        c,p,g=self.fixture()
        for family in ['balance','vacuum']:
            cohort,pp,gg=inputs_for({'cohort':['a']},c,p,g,family)
            self.assertEqual(len(pp['requests']),8)
            self.assertEqual(len(gg['gate_events']),1)
            self.assertTrue(all(r['request_id'].startswith(('parent:',family+':')) for r in pp['requests']))
    def test_missing_parent_never_dropped(self):
        c,p,g=self.fixture();del p['parent','a']
        with self.assertRaises(ValueError):inputs_for({'cohort':['a']},c,p,g,'balance')
    def test_incomplete_horizon_fails(self):
        c,p,g=self.fixture();del p['balance','a'][600]
        with self.assertRaises(ValueError):inputs_for({'cohort':['a']},c,p,g,'balance')
    def test_vacuum_phase_maps_to_existing_leg_engine(self):
        tape=[dict(type='book',market_id='m',recv_ms=100,down_asks=[[.8,100]]),dict(type='decision',market_id='m',recv_ms=100,features=dict(experiment_kind=2,experiment_side=-1,tau_s=20))]
        out=list(route(tape,[dict(market_id='m',decision_ms=0)],'vacuum'))
        self.assertEqual(len([r for r in out if r['type']=='decision']),4)

if __name__=='__main__':unittest.main()
