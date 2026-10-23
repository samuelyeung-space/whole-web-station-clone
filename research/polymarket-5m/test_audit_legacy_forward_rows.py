import copy
import unittest
from audit_legacy_forward_rows import audit


def row(t='100'):
    return dict(variant='G', lag='0.5', run='123456789',
                market_id='btc-updown-5m-100', t=t, side='Up',
                price='0.4', fee='0.0168', size='100', won='1',
                pnl='0.5832', p0='0.4', fair='0.7')


class AuditTests(unittest.TestCase):
    def test_append_and_numeric_format_are_equivalent(self):
        old=row(); same=row();same['price']='0.400000'
        result=audit([old],[same,row('101')])
        self.assertTrue(result['passed']);self.assertEqual(len(result['added_keys']),1)
        self.assertIsNone(result['dollar_pnl'])

    def test_depth_rewrite_does_not_pass(self):
        new=row();new['size']='90'
        result=audit([row()],[new])
        self.assertFalse(result['passed'])
        self.assertIn('size',result['changed_existing_rows'][0]['changed_fields'])

    def test_tiny_drift_reported_separately_but_immutable_gate_fails(self):
        new=row();new['fair']='0.7000000000000001'
        result=audit([row()],[new])
        self.assertFalse(result['passed'])
        self.assertEqual(result['tiny_numeric_changed_rows'],1)
        self.assertEqual(result['material_changed_rows'],0)

    def test_removed_or_duplicate_rows_fail(self):
        self.assertFalse(audit([row(),row('101')],[row()])['passed'])
        self.assertFalse(audit([row()],[row(),copy.copy(row())])['passed'])

    def test_fee_and_pnl_fail_separately(self):
        new=row();new['fee']='0';new['pnl']='0.6'
        result=audit([row()],[new])
        self.assertIn('fee_equation_mismatch',[x['reason'] for x in result['new_errors']])
        new=row();new['pnl']='100'
        self.assertIn('per_unit_pnl_mismatch',[x['reason'] for x in audit([row()],[new])['new_errors']])

    def test_empty_nonfinite_and_nonbtc_fail(self):
        self.assertFalse(audit([],[])['passed'])
        for key,value in [('size','nan'),('market_id','eth-updown-5m-100'),('won','0.5')]:
            new=row();new[key]=value
            self.assertFalse(audit([row()],[new])['passed'])


if __name__ == '__main__':
    unittest.main()
