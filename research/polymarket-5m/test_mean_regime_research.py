import unittest,tempfile,gzip,json
import numpy as np
from mean_regime_research import build_price_grid,signal
class Tests(unittest.TestCase):
 def test_price_receipt_and_staleness(self):
  with tempfile.NamedTemporaryFile(suffix='.gz') as f:
   with gzip.open(f.name,'wt') as out:
    for r in [{'receive_ts':1.2,'trade_ts':1.,'price':100},{'receive_ts':2.2,'trade_ts':2.,'price':110},{'receive_ts':2.5,'trade_ts':.9,'price':999}]:out.write(json.dumps(r)+'\n')
   a=build_price_grid(f.name,[1000,2000,3000,6000]);self.assertTrue(np.isnan(a[0]));self.assertEqual(a[1],100);self.assertEqual(a[2],110);self.assertTrue(np.isnan(a[3]))
 def test_far_mean_is_not_automatic_fade(self):
  c={'mechanism':'flat_overshoot','edge':.04,'smooth_only':False,'window':60,'band':1.,'reference_slope_gate':False}
  f={'book_5':.8,'ask_up':.5,'ask_down':.51,'fee':.07,'tau_s':100,'z60':-2.,'slope60':-2.,'refslope60':-1.,'btc_r5':-1.,'poly_eff':.1,'poly_move':0,'imbalance':.5}
  self.assertIsNone(signal(c,f));f['slope60']=.1;self.assertEqual(signal(c,f),'Up')
if __name__=='__main__':unittest.main()
