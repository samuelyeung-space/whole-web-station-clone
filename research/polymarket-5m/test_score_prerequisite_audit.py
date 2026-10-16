import unittest
from pathlib import Path
import ast,math,random
from score_prerequisite_audit import NEED, Trace, prerequisites


def decision(t=1000, score=False, mid='A'):
    f={n:.1 for n in NEED};f['tau_s']=200
    if score:f['book_5']=.6
    return {'type':'decision','market_id':mid,'recv_ms':t,'features':f,'feature_available_ms':{n:t for n in f}}


def book(t=1000, mid='A'):
    return {'type':'book','market_id':mid,'recv_ms':t,'up_recv_ms':t,'down_recv_ms':t,
        'healthy':True,'fee_rate':.07,'up_asks':[[.51,100]],'up_bids':[[.49,100]],'down_asks':[[.51,100]]}


class PrerequisiteTests(unittest.TestCase):
    def test_fresh_book(self):
        self.assertEqual(prerequisites(decision(),book()),[])

    def test_250ms_age_not_venue_wait(self):
        b=book();b['down_recv_ms']=750
        self.assertEqual(prerequisites(decision(),b),[])
        b['down_recv_ms']=749
        self.assertIn('book:down:quote_older_than250ms',prerequisites(decision(),b))

    def test_missing_and_future_inputs(self):
        e=decision();e['features']['ep_r_60s']=None;e['feature_available_ms']['ep_r_5s']=1001
        b=book();b['up_recv_ms']=None;b['down_recv_ms']=1001
        r=prerequisites(e,b)
        self.assertIn('feature:ep_r_60s:missing',r)
        self.assertIn('feature:ep_r_5s:unavailable',r)
        self.assertIn('book:up:receipt_missing',r)
        self.assertIn('book:down:future_receipt',r)

    def test_do_not_clear_history_for_one_bad_second(self):
        a=Trace();a.feed(book(1000));a.feed(decision(1000))
        a.feed(book(2000));e=decision(2000);e['features']['mid_up']=None;a.feed(e)
        a.feed(book(3000));a.feed(decision(3000,score=True))
        self.assertIn(('A',3000),a.eligible_keys)

    def test_long_gap_initializes_again(self):
        a=Trace();a.feed(book(1000));a.feed(decision(1000))
        a.feed(book(4000));a.feed(decision(4000))
        self.assertEqual(a.eligible_keys,set())

    def test_market_isolation_and_duplicates(self):
        a=Trace();a.feed(book(1000));a.feed(decision(1000))
        a.feed(book(2000,'B'));a.feed(decision(2000,mid='B'))
        self.assertEqual(a.eligible_keys,set())
        with self.assertRaises(ValueError):a.feed(decision(2000,mid='B'))

    def test_healthy_without_down_not_admitted(self):
        b=book();b['down_asks']=[]
        self.assertIn('book:down_asks:empty',prerequisites(decision(),b))

    def test_outcome_is_not_inspected(self):
        class Forbidden(dict):
            def __getitem__(self,k):
                if k != 'type':raise AssertionError('outcome read')
                return 'outcome'
        Trace().feed(Forbidden())

    def test_random_admission_keys_match_pinned_original_function(self):
        path=Path(__file__).with_name('joint_probability_research.py')
        if not path.exists():
            path=Path(__file__).with_name('upstream_reference')/'joint_probability_research.py'
        # Optional fixture is fetched from the recorded source SHA; never
        # require network or sklearn for the portable unit test suite.
        if not path.exists():self.skipTest('pinned upstream differential fixture not supplied')
        from score_prerequisite_audit import sha,UPSTREAM_SHA256
        self.assertEqual(sha(path),UPSTREAM_SHA256)
        tree=ast.parse(path.read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('finite','features')]
        rng=random.Random(17);events=[];t=1000
        for i in range(1000):
            t+=rng.choice([1000,1000,1000,2000,3000]);mid=rng.choice(['A','B'])
            b=book(t,mid);e=decision(t,mid=mid)
            mutation=rng.randrange(8)
            if mutation==0:e['features']['ep_r_60s']=None
            elif mutation==1:b['healthy']=False
            elif mutation==2:b['down_recv_ms']=t-251
            elif mutation==3:b['down_recv_ms']=t+1
            elif mutation==4:b['down_asks']=[]
            elif mutation==5:e['feature_available_ms']['mid_up']=t+1
            events.extend([b,e])
        scope={'math':math,'events':lambda _:iter(events)}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),scope)
        rows,labels,_=scope['features']([])
        a=Trace()
        for e in events:a.feed(e)
        self.assertFalse(labels)
        self.assertEqual(a.eligible_keys,{(r['market'],r['t']) for r in rows})


if __name__=='__main__':unittest.main()
