import unittest
from audit_special_case_intents import Minutes
def trade(t,p=100):return dict(kind='spot_trade',recv_ms=t,connection_epoch=1,price=p)
class Tests(unittest.TestCase):
 def baseline(self):
  x=Minutes();x.ingest(dict(kind='spot_connection',recv_ms=-1000,connection_epoch=1))
  for t in range(-500,60000,500):x.ingest(trade(t))
  return x
 def test_boundary_trade_does_not_enter_closed_bar(self):
  x=self.baseline();x.ingest(trade(60000,999));b=x.close(0)
  self.assertTrue(b['complete']);self.assertEqual(b['high'],100)
 def test_disconnect_retained(self):
  x=self.baseline();x.ingest(dict(kind='spot_disconnect',recv_ms=59999,connection_epoch=1))
  self.assertFalse(x.close(0)['complete'])
 def test_gap_and_missing_start_rejected(self):
  x=Minutes();x.ingest(dict(kind='spot_connection',recv_ms=-1000,connection_epoch=1));x.ingest(trade(2000));x.ingest(trade(59000))
  self.assertFalse(x.close(0)['complete']);self.assertIn('trade_gap',x.close(0)['reasons'])
 def test_stale_end_and_regression(self):
  x=self.baseline();self.assertFalse(x.close(60000)['complete'])
  with self.assertRaises(ValueError):x.ingest(trade(0))
if __name__=='__main__':unittest.main()
