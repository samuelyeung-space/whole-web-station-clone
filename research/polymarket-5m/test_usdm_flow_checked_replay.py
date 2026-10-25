"""Synthetic missing-decision counterexample, not historical strategy evidence."""
import copy
import unittest
from unittest.mock import patch
from test_usdm_flow_execution_bridge import fixture
from usdm_flow_execution_bridge import replay_development
import usdm_flow_checked_replay as c


def incomplete():
    e,b=fixture();e[0]['coverage_end_ms']=582000
    x,_=fixture()
    for a in x[1:]:
        a['market_id']='missing-binding'
        for k in ('recv_ms','start_ms','end_ms','resolved_ms'):
            if k in a:a[k]+=300000
    e.extend(x[1:]);e.sort(key=lambda a:a['recv_ms'])
    return replay_development(e,b,split='development',input_evidence_class='synthetic_fixture')


class Completeness(unittest.TestCase):
    def test_actual_engine_counterexample_vetoes_partial_known_pnl(self):
        r=incomplete();prior=copy.deepcopy(r)
        original=[x for x in r['scenarios']['base']['summary'] if x['status']=='simulated_complete']
        self.assertTrue(original);self.assertTrue(all(x['invalid_decision_reasons'] for x in original))
        a=c.audit_result(r)
        self.assertEqual(r,prior)
        self.assertTrue(all(x['status']=='incomplete_invalid_decision_inputs' and x['net_pnl_usd'] is None for x in a['admitted_development_summary']))
        self.assertEqual(a['raw_development_diagnostics'],prior)
        self.assertFalse(a['main_ranking_admitted'])
    def test_complete_fixture_retains_development_only_pnl(self):
        e,b=fixture();r=replay_development(e,b,split='development',input_evidence_class='synthetic_fixture')
        a=c.audit_result(r);good=[x for x in a['admitted_development_summary'] if x['complete_development_simulation']]
        self.assertTrue(good);self.assertTrue(all(x['net_pnl_usd'] is not None for x in good));self.assertIsNone(a['formal_oos_result'])
    def test_veto_not_conditioned_on_sign_or_fill_status(self):
        r=incomplete()
        for cell in r['scenarios'].values():
            for row in cell['summary']:
                row.update(status='evaluated_no_fills',net_pnl_usd=0.)
        a=c.audit_result(r);self.assertTrue(all(x['net_pnl_usd'] is None for x in a['admitted_development_summary']))
        for cell in r['scenarios'].values():
            for row in cell['summary']:row.update(status='simulated_complete',net_pnl_usd=-99.)
        a=c.audit_result(r);self.assertTrue(all(x['net_pnl_usd'] is None for x in a['admitted_development_summary']))
    def test_missing_or_invalid_counters_cannot_certify(self):
        for value in (None,{'bad':-1},{'bad':True}):
            r=incomplete();r['scenarios']['base']['summary'][0]['invalid_decision_reasons']=value
            with self.assertRaises(ValueError):c.audit_result(r)
    def test_file_entry_applies_audit(self):
        r=incomplete()
        with patch.object(c.bundle,'replay_bundle',return_value=r) as run:
            a=c.checked_replay('unused',expected_manifest_sha256='expected')
            run.assert_called_once_with('unused',expected_manifest_sha256='expected')
            self.assertTrue(all(x['net_pnl_usd'] is None for x in a['admitted_development_summary']))

if __name__=='__main__':unittest.main()
