import random,unittest
from absorption import TokenBook
from book_batch_intake import BatchGates
from book_batch_fast import FastBatchGates


class EquivalentTests(unittest.TestCase):
    def test_deterministic_streams_match_legacy_states_and_gate_events(self):
        for seed in range(5):
            rng=random.Random(seed)
            arms=[dict(arm_id=str(i),asset_id=str(i%3),market_id='m',side='Up',decision_ms=250+i*113) for i in range(60)]
            old,new=BatchGates(arms),FastBatchGates(arms)
            epoch=1
            for t in range(0,40001,100):
                lifecycle=rng.random()<.003
                if lifecycle:epoch+=1
                for d in (old,new):d.before_group(t,epoch,lifecycle)
                for asset in ['0','1','2']:
                    opposed=rng.random()<.3;bad=rng.random()<.005
                    book=TokenBook(bids={.49:25 if opposed else 100},asks={.51:175 if opposed else 100},ready=True)
                    obs=dict(asset_id=asset,book=book,recv_ms=t,epoch=epoch,update_id=t,validated=not bad,ambiguous_receipt=False)
                    for d in (old,new):d.observe(obs)
            self.assertEqual(old.g.arms,new.g.arms)
            order=lambda e:(e['recv_ms'],e['parentkey'],e['status'])
            # Set iteration order across independent simultaneous parent watches
            # is immaterial; each parent's ordered event history must be exact.
            self.assertEqual(sorted(old.gates,key=order),sorted(new.gates,key=order))
            for key in old.g.arms:
                self.assertEqual([e for e in old.g.events if e['parentkey']==key],
                                 [e for e in new.g.events if e['parentkey']==key])


if __name__=='__main__':unittest.main()
