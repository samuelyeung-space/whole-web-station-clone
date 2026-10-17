import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from replay_input_preflight import audit_bbo,audit_freeze,bbo_issues,logical_changes


class PreflightTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
 def freeze(self,dependencies):
  p=self.root/'freeze.json';p.write_text(json.dumps(dict(dependencies_sha256=dependencies)))
  return p,hashlib.sha256(p.read_bytes()).hexdigest()
 def stream(self,rows,compressed=True,raw=None):
  p=self.root/('input.jsonl.gz' if compressed else 'input.jsonl')
  b=raw if raw is not None else ''.join(json.dumps(x)+'\n' for x in rows).encode()
  p.write_bytes(gzip.compress(b) if compressed else b);return p
 def test_all_dependency_drift_is_reported(self):
  (self.root/'a.py').write_bytes(b'a\n');(self.root/'b.py').write_bytes(b'b\n')
  f,h=self.freeze({'a.py':hashlib.sha256(b'a\n').hexdigest(),'b.py':'0'*64,'missing.py':'1'*64})
  r=audit_freeze(self.root,f,h)
  self.assertEqual(r['matches'],1);self.assertEqual(r['checked'],3)
  self.assertEqual([x['status'] for x in r['dependencies']],['match','drift','missing'])
  self.assertFalse(r['formal_eligibility'])
 def test_manifest_drift_fails_closed(self):
  f,h=self.freeze({'a.py':'0'*64})
  with self.assertRaisesRegex(ValueError,'fingerprint'):audit_freeze(self.root,f,'1'*64)
 def test_traversal_fails_closed(self):
  f,h=self.freeze({'../outside':'0'*64})
  with self.assertRaisesRegex(ValueError,'unsafe'):audit_freeze(self.root,f,h)
 def test_symlink_not_followed(self):
  (self.root/'target').write_bytes(b'x');(self.root/'link').symlink_to(self.root/'target')
  f,h=self.freeze({'link':hashlib.sha256(b'x').hexdigest()})
  self.assertEqual(audit_freeze(self.root,f,h)['dependencies'][0]['status'],'symlink_refused')
 def test_duplicate_freeze_keys_rejected(self):
  f=self.root/'freeze.json';f.write_bytes(b'{"dependencies_sha256":{"a":"x","a":"y"}}')
  with self.assertRaisesRegex(ValueError,'duplicate'):audit_freeze(self.root,f,hashlib.sha256(f.read_bytes()).hexdigest())
 def test_valid_and_one_sided_quotes(self):
  self.assertEqual(bbo_issues({'best_bid':'.4','best_ask':'.6'}),[])
  self.assertEqual(bbo_issues({'best_bid':'.4'}),[]);self.assertEqual(bbo_issues({}),[])
 def test_boundary_prices_not_repaired(self):
  r={'best_bid':'0','best_ask':'1'};before=dict(r)
  self.assertEqual(bbo_issues(r),['best_bid_boundary_zero','best_ask_boundary_one']);self.assertEqual(r,before)
 def test_crossed_and_locked(self):
  for b,a in [('.5','.5'),('.6','.4')]:
   self.assertIn('locked_or_crossed_reported_bbo',bbo_issues({'best_bid':b,'best_ask':a}))
 def test_bad_numeric_forms(self):
  for v,reason in [(None,'conversion_error'),('', 'conversion_error'),('nan','nonfinite'),('inf','nonfinite'),('-1','outside_unit_interval')]:
   self.assertIn('best_bid_'+reason,bbo_issues({'best_bid':v}))
 def test_raw_scan_counts_without_exposing_quote_values(self):
  p=self.stream([{'recv_ms':1,'msg':[{'event_type':'price_change','price_changes':[{'best_bid':0,'best_ask':1},{'best_bid':.123456789,'best_ask':.8}]}]}])
  before=hashlib.sha256(p.read_bytes()).hexdigest();r=audit_bbo(p)
  self.assertEqual(r['counts']['numeric_guard_reject'],1);self.assertEqual(r['counts']['numeric_guard_pass'],1)
  self.assertTrue(r['stream_eof_crc_checked']);self.assertFalse(r['eligibility_filters_applied'])
  self.assertNotIn('.123456789',json.dumps(r));self.assertEqual(before,hashlib.sha256(p.read_bytes()).hexdigest())
 def test_strict_batch_and_no_bbo(self):
  p=self.stream([{'kind':'clob_price_change_batch','changes':[{'price':'.4'},{'best_bid':.4,'best_ask':.4}]}])
  r=audit_bbo(p,'strict');self.assertEqual(r['counts']['changes_without_reported_bbo'],1)
  self.assertEqual(r['counts']['numeric_guard_reject'],1)
 def test_truncated_gzip_is_not_complete(self):
  p=self.stream([{'msg':[]}]);p.write_bytes(p.read_bytes()[:-4])
  r=audit_bbo(p);self.assertIsNotNone(r['error']);self.assertFalse(r['stream_eof_crc_checked'])
 def test_unterminated_and_malformed_rows(self):
  for raw in [b'{}',b'{bad}\n']:
   r=audit_bbo(self.stream([],raw=raw));self.assertIsNotNone(r['error'])
 def test_invalid_container_and_batch_rejected(self):
  with self.assertRaisesRegex(ValueError,'raw msg'):list(logical_changes({'msg':'PONG'},'raw'))
  with self.assertRaisesRegex(ValueError,'strict batch'):list(logical_changes({'kind':'clob_price_change_batch','changes':[]},'strict'))
 def test_numeric_rejection_parity_with_legacy_guard(self):
  absent=object();values=[absent,None,'','nan','inf','-inf',-1,0,.0001,.4,.5,.9999,1,2,True,False,'0.25']
  def legacy_reject(row):
   try:
    bid=float(row['best_bid']) if 'best_bid' in row else None
    ask=float(row['best_ask']) if 'best_ask' in row else None
    return bool((bid is not None and (not math.isfinite(bid) or not 0<bid<1)) or
                (ask is not None and (not math.isfinite(ask) or not 0<ask<1)) or
                (bid is not None and ask is not None and bid>=ask))
   except (TypeError,ValueError,OverflowError):return True
  for b in values:
   for a in values:
    row={}
    if b is not absent:row['best_bid']=b
    if a is not absent:row['best_ask']=a
    self.assertEqual(bool(bbo_issues(row)),legacy_reject(row),repr(row))


if __name__=='__main__':unittest.main()
