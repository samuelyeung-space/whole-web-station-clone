import unittest
from book_parent_cohorts import compile_cohorts


def market():return dict(type='market',recv_ms=0,market_id='m',start_ms=0,end_ms=300000,label_source='gamma',up_token_id='u',down_token_id='d')
def decision(t=100,future=False):return dict(type='decision',market_id='m',recv_ms=t,features=dict(tau_s=(300000-t)/1000,x=1),feature_available_ms=dict(tau_s=t,x=t+1 if future else t))
def candidate(name='c',features=None):return dict(id=name,required_features=features or ['x'])
def buy(c,f):return 'Up'


class CohortTests(unittest.TestCase):
    def test_unhealthy_first_signal_is_preserved(self):
        rows=[market(),dict(type='book',recv_ms=50,healthy=False),decision(100),decision(200)]
        r=compile_cohorts(rows,[candidate()],buy)
        self.assertEqual(len(r['unique_arms']),1);self.assertEqual(next(iter(r['unique_arms'].values()))['decision_ms'],100)

    def test_future_feature_is_unavailable_but_not_global_block(self):
        r=compile_cohorts([market(),decision(100,True),decision(200)],[candidate()],buy)
        self.assertEqual(next(iter(r['unique_arms'].values()))['decision_ms'],200)
        self.assertEqual(r['candidate_records'][0]['rejection_reasons']['future_feature:x'],1)

    def test_missing_feature_blocks_only_dependent_candidate(self):
        r=compile_cohorts([market(),decision()],[candidate(),candidate('blocked',['missing'])],buy)
        self.assertEqual([c['first_signal_markets'] for c in r['candidate_records']],[1,0])

    def test_identical_cohorts_keep_all_members_and_deduplicate_input(self):
        r=compile_cohorts([market(),decision()],[candidate(),candidate('d')],buy)
        self.assertEqual(len(r['unique_arms']),1);self.assertEqual(next(iter(r['identical_cohort_groups'].values())),['c','d'])

    def test_wrong_tau_and_regressed_receipts_rejected(self):
        d=decision();d['features']['tau_s']=1
        self.assertEqual(compile_cohorts([market(),d],[candidate()],buy)['unique_arms'],{})
        with self.assertRaises(ValueError):compile_cohorts([market(),decision(200),decision(100)],[candidate()],buy)


if __name__=='__main__':unittest.main()
