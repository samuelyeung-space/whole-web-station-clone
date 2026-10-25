"""Synthetic failure counterexamples only; zero historical financial replays."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import recorder_flow_file_audit as a
from recorder_futures_flow import audit_file as old_audit


def row(i=1, recv=1000, **changes):
    r = dict(kind='trade',s='BTCUSDT',recv_ms=recv,T=1000,E=1000,t=i,
             p='100',q='1',m=False,connection_epoch=1)
    r.update(changes); return r


class AuditFailures(unittest.TestCase):
    def run_fixture(self, rows, old=False, **changes):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'fixture.jsonl.gz'
            with gzip.open(p,'wt') as f:
                for r in rows: f.write((r if isinstance(r,str) else json.dumps(r))+'\n')
            args=dict(expected_sha256=a.digest_file(p),capture_run_id='synthetic')
            if old:return old_audit(p,**args)
            args['split']='development';args.update(changes)
            return a.audit_file(p,**args)

    def assert_blocked(self, rows, stage):
        r=self.run_fixture(rows+[row(99,5000)])
        self.assertEqual(r['first_failure']['stage'],stage)
        self.assertEqual(r['status'],'blocked_input_error')
        self.assertEqual(r['post_failure_rows_used_for_features'],0)
        self.assertEqual(r['counts']['standalone_parseable_btc_rows_after_failure_diagnostic_only'],1)
        self.assertFalse(r['features_emitted']);self.assertIsNone(r['pnl_usd'])

    def test_conflicting_id_old_audit_raises_new_audit_retains_diagnostic(self):
        rows=[row(),row(recv=1100,q='2')]
        with self.assertRaises(ValueError):self.run_fixture(rows,old=True)
        self.assert_blocked(rows,'tape')

    def test_epoch_switch_old_audit_raises_new_audit_retains_diagnostic(self):
        rows=[row(),row(2,1100,connection_epoch=2)]
        with self.assertRaises(ValueError):self.run_fixture(rows,old=True)
        self.assert_blocked(rows,'tape')

    def test_receive_clock_regression(self):
        self.assert_blocked([row(),row(2,900)],'tape')

    def test_malformed_json_and_nonobject(self):
        for value,stage in [('{bad','json'),([],'shape')]:
            with self.subTest(stage=stage):self.assert_blocked([row(),value],stage)

    def test_bad_quantity_and_first_failure_stays_first(self):
        r=self.run_fixture([row(q='0'),row(2,1100,q='-1'),row(3,1200)])
        self.assertEqual(r['first_failure']['line_number'],1)
        self.assertEqual(r['counts']['rejected_rows'],2)
        self.assertEqual(r['counts']['standalone_parseable_btc_rows_after_failure_diagnostic_only'],1)

    def test_exact_duplicate_is_not_claimed_as_unique_or_coverage(self):
        r=self.run_fixture([row(),row(),dict(kind='heartbeat')])
        self.assertIsNone(r['first_failure']);self.assertEqual(r['counts']['tape_add_trade_calls_before_failure'],2)
        self.assertEqual(r['coverage_certificates_produced'],0)
        self.assertEqual(r['status'],'no_detected_error_coverage_unverified')

    def test_hash_and_split_guard_before_parse(self):
        with self.assertRaisesRegex(ValueError,'SHA256'):self.run_fixture(['bad'],expected_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'development'):self.run_fixture(['bad'],split='OOS')


if __name__=='__main__':unittest.main()
