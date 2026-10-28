import unittest
from special_case_trend import MultiTimeframe,SpecialCase


def opening():return dict({str(k):dict(ctx_epoch=1,ctx_st_direction=1,atr=10) for k in (300000,900000)},asof_ms=0)
def bar(i,o,c):return dict(start_ms=i*60000,end_ms=(i+1)*60000,open=o,close=c,high=max(o,c),low=min(o,c),complete=True,available_ms=(i+1)*60000)
class Tests(unittest.TestCase):
 def test_isolation_and_parameters(self):
  x=MultiTimeframe();self.assertEqual([c.audit()['parameters']['bar_ms'] for c in x.contexts.values()],[300000,900000])
 def test_future_receipt_never_visible(self):
  x=MultiTimeframe();x.ingest(dict(kind='spot_connection',recv_ms=100,connection_epoch=1));r=x.snapshot(0)
  self.assertIsNone(r['300000']['ctx_epoch'])
 def test_three_rebound_four_stall(self):
  x=SpecialCase(0,opening(),100)
  self.assertEqual(x.closed_minute(bar(0,100,99),60000,(1,1)),[])
  self.assertEqual(x.closed_minute(bar(1,99,98),120000,(1,1)),[])
  self.assertEqual(x.closed_minute(bar(2,98,101),180000,(1,1))[0]['kind'],'ENTRY_INTENT')
  self.assertEqual(x.closed_minute(bar(3,101,100),240000,(1,1))[0]['kind'],'EXIT_OR_HEDGE_INTENT')
 def test_future_bar_and_epoch_fail_closed(self):
  for mode in ['future','epoch','missing']:
   x=SpecialCase(0,opening(),100);b=bar(0,100,99)
   if mode=='future':b['available_ms']=60001
   if mode=='missing':b['complete']=False
   self.assertEqual(x.closed_minute(b,60000,(2,1) if mode=='epoch' else (1,1)),[]);self.assertIsNotNone(x.reason)
 def test_no_calm_or_no_rebound_no_entry(self):
  x=SpecialCase(0,opening(),100)
  for i,(o,c) in enumerate([(100,90),(90,89),(89,101)]):self.assertEqual(x.closed_minute(bar(i,o,c),(i+1)*60000,(1,1)),[])
 def test_future_opening_context_rejected(self):
  c=opening();c['asof_ms']=1;x=SpecialCase(0,c,100)
  self.assertIsNotNone(x.reason)
 def test_disagreement_not_rearmed(self):
  c=opening();c['900000']['ctx_st_direction']=-1;x=SpecialCase(0,c,100)
  self.assertEqual(x.closed_minute(bar(0,100,99),60000,(1,1)),[])
if __name__=='__main__':unittest.main()
