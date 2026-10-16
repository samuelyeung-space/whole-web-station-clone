import unittest,copy
from unittest.mock import patch
import joint_probability_research as j

def tape():
 out=[{'type':'market','market_id':'m','recv_ms':0,'end_ms':300000}]
 for i in range(1,40):
  t=i*1000;p=.4+i*.001
  out.append({'type':'book','market_id':'m','recv_ms':t,'up_recv_ms':t,'down_recv_ms':t,'healthy':True,'fee_rate':.07,'up_asks':[[p+.01,50+i]],'up_bids':[[p-.01,70-i]],'down_asks':[[1-p+.01,50]]})
  f={'mid_up':p,'tau_s':300-i,'ep_r_5s':1.,'ep_r_15s':2.,'ep_r_60s':3.,'ep_r_30s':2.5,'imbalance_up':.2}
  out.append({'type':'decision','market_id':'m','recv_ms':t,'features':f,'feature_available_ms':{k:t for k in f}})
 return out
class Tests(unittest.TestCase):
 def runfeatures(self,ev):
  with patch.object(j,'events',lambda _:iter(ev)):return j.features([])[0]
 def test_prefix(self):
  e=tape();prefix=[r for r in e if r['recv_ms']<=20000]
  self.assertEqual(self.runfeatures(prefix),[r for r in self.runfeatures(e) if r['t']<=20000])
 def test_future_label(self):
  e=tape();x=self.runfeatures(e)
  e.append({'type':'outcome','market_id':'m','recv_ms':400000,'resolved_ms':400000,'winner':'Down'})
  self.assertEqual(x,self.runfeatures(e))
 def test_future_feature_rejected(self):
  e=tape();e[-1]['feature_available_ms']['ep_r_5s']=999999
  self.assertNotIn(39000,[r['t'] for r in self.runfeatures(e)])
 def test_gap_reset(self):
  e=[r for r in tape() if not 15000<=r['recv_ms']<=20000]
  self.assertNotIn(21000,[r['t'] for r in self.runfeatures(e)])
 def test_no_fictional_book_flow(self):
  rows=self.runfeatures(tape());self.assertTrue(rows)
  self.assertEqual(len(rows[-1]['vectors']['joint_5']),23)
if __name__=='__main__':unittest.main()
