import unittest,math
from mlq_first_pullback_features import closed_bars,features,PullbackState
from mlq_first_pullback import signal,candidates
class CausalTests(unittest.TestCase):
 def test_boundary_and_no_open_bar(self):
  r=[dict(receive_ts=t,trade_ts=t,price=p) for t,p in [(0.1,100),(.9,101),(1.,999),(2.1,102)]]
  self.assertEqual(list(closed_bars(r))[:2],[(1000,100.,101.),(2000,999.,999.)])
  self.assertIn((3000,102.,102.),list(closed_bars(r)))
 def test_future_append_does_not_change_prefix(self):
  r=[dict(receive_ts=i+.1,trade_ts=i,price=100+math.sin(i/9)+i*.01) for i in range(300)]
  a=features(r[:200]);b=features(r)
  self.assertEqual({k:v for k,v in a.items() if k<=199100},{k:v for k,v in b.items() if k<=199100})
 def test_source_timestamp_never_admits_future_receipt(self):
  r=[dict(receive_ts=i+.1,trade_ts=i,price=100+i*.01) for i in range(150)]
  a=features(r);r.append(dict(receive_ts=200,trade_ts=1,price=100000))
  b=features(r);self.assertEqual({k:v for k,v in a.items() if k<=149100},{k:v for k,v in b.items() if k<=149100})
 def test_stale_only_bucket_carries_price_not_none(self):
  rows=[dict(receive_ts=.1,trade_ts=10,price=100),dict(receive_ts=1.1,trade_ts=9,price=999),dict(receive_ts=2.1,trade_ts=11,price=101)]
  self.assertEqual(list(closed_bars(rows))[1],(2000,100.,100.))
 def test_gap_resets_warmup(self):
  st=PullbackState()
  for i in range(121):st.step(i*1000,100,100+i*.01,1)
  self.assertIsNone(st.step(130000,101,101,1));self.assertIsNone(st.anchor)
 def test_first_vs_all_only_ordinal(self):
  f=dict(book_5=.8,ask_up=.5,ask_down=.5,fee=.07,tau_s=120,mlq1_direction=1,mlq1_ordinal=2)
  a,b=candidates()[:2];self.assertIsNone(signal(a,f));self.assertEqual(signal(b,f),'Up')
  f['mlq1_ordinal']=1;self.assertEqual(signal(a,f),signal(b,f))
if __name__=='__main__':unittest.main()
