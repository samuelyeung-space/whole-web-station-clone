import copy
import unittest
import depth_pressure_persistence as d


def book(t,up=(90,10),down=(10,90)):
    return dict(type='book',recv_ms=t,market_id='m',healthy=True,fee_rate=.07,
        up_recv_ms=t,down_recv_ms=t,
        up_bids=[[.49,up[0]]],up_asks=[[.51,up[1]]],
        down_bids=[[.49,down[0]]],down_asks=[[.51,down[1]]])


def events(times=(0,1000,2000,3000)):
    rows=[dict(type='dataset',recv_ms=0,split='development',dataset_id='synthetic',provenance={'source':'test'})]
    for t in times:
        rows.extend([book(t),dict(type='decision',recv_ms=t,market_id='m',features={'tau_s':100},
                                feature_available_ms={'tau_s':t})])
    return rows


class DepthPressureTests(unittest.TestCase):
    def test_catalog(self):
        c=d.candidates();self.assertEqual(len(c),24)
        self.assertEqual(len({x['rule_fingerprint'] for x in c}),24)
        self.assertEqual(len({x['id'] for x in c}),24)
        self.assertFalse(any(x['independent_mechanism_claim'] for x in c))

    def test_complete_path_and_no_input_mutation(self):
        raw=events();prior=copy.deepcopy(raw);out=list(d.augment(raw))
        self.assertEqual(raw,prior)
        dec=[r for r in out if r['type']=='decision']
        self.assertTrue(all(r['features']['dpp_joint_3s'] is None for r in dec[:-1]))
        self.assertAlmostEqual(dec[-1]['features']['dpp_joint_3s'],.8)
        self.assertEqual(dec[-1]['feature_available_ms']['dpp_joint_3s'],3000)
        self.assertIsNone(dec[-1]['features']['dpp_joint_5s'])
        for a,b in zip(raw,out):
            if a['type']!='decision':self.assertIs(a,b)

    def test_gap_no_interpolation(self):
        out=list(d.augment(events((0,1000,3000,4000,5000,6000))))
        dec=[r for r in out if r['type']=='decision']
        self.assertIsNone(dec[-2]['features']['dpp_joint_3s'])
        self.assertAlmostEqual(dec[-1]['features']['dpp_joint_3s'],.8)

    def test_stale_side_resets_without_future_health_filter(self):
        rows=events((0,1000,2000,3000,4000));rows[-4]['down_recv_ms']=0
        dec=[r for r in d.augment(rows) if r['type']=='decision']
        self.assertIsNone(dec[-1]['features']['dpp_joint_3s'])
        self.assertIsNone(dec[-2]['features']['dpp_joint_3s'])

    def test_joint_mirror_and_common_mode_control(self):
        b=book(0);q,u,p=d.snapshot(b,0)
        swapped={**b,'up_bids':b['down_bids'],'up_asks':b['down_asks'],
                     'down_bids':b['up_bids'],'down_asks':b['up_asks']}
        q2,u2,p2=d.snapshot(swapped,0)
        self.assertAlmostEqual(q2,-q);self.assertAlmostEqual(p2,1-p)
        q,u,p=d.snapshot(book(0,down=(90,10)),0)
        self.assertEqual(q,0);self.assertAlmostEqual(u,.8)

    def test_bad_book_and_clocks_fail_closed(self):
        for field,value in [('up_bids',[[0,1]]),('down_asks',[[.5,True]]),
                            ('up_bids',[[.49,1],[.49,2]]),('down_asks',[[float('nan'),1]]),
                            ('up_bids',[[.6,1]]),('up_recv_ms',1),('down_recv_ms',-251),
                            ('up_healthy',False),('recv_ms',1)]:
            b=book(0);b[field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):d.snapshot(b,0)

    def test_prefix_causality(self):
        raw=events();prefix=list(d.augment(raw))
        extended=raw+[dict(type='outcome',recv_ms=100000,market_id='m',winner='Down')]
        self.assertEqual(prefix,list(d.augment(extended))[:len(prefix)])

    def test_collision_and_protected_split_rejected(self):
        raw=events();raw[-1]['features']['dpp_joint_3s']=1
        with self.assertRaisesRegex(ValueError,'collision'):list(d.augment(raw))
        raw=events();raw[0]['split']='oos'
        with self.assertRaisesRegex(ValueError,'development'):list(d.augment(raw))

    def test_signal_directions_and_threshold_boundaries(self):
        for c in d.candidates():
            s=c['seconds'];f={f'dpp_{c["arm"]}_{s}s':c['threshold'],
                f'dpp_net_{s}s':.02 if c['template']=='continuation' else -.02,
                f'dpp_range_{s}s':.01 if c['template']=='flat' else .02,'tau_s':100}
            self.assertEqual(d.signal(c,f),'Up')
            f[f'dpp_{c["arm"]}_{s}s']*=-1;f[f'dpp_net_{s}s']*=-1
            self.assertEqual(d.signal(c,f),'Down')
            f['tau_s']=10;self.assertIsNone(d.signal(c,f))

    def test_pressure_minimum_not_mean(self):
        self.assertEqual(d.persistent([.8,.2,.8,.8]),.2)
        self.assertEqual(d.persistent([-.8,-.2,-.8]),-.2)
        self.assertEqual(d.persistent([.8,-.2,.8]),0)


if __name__=='__main__':unittest.main()
