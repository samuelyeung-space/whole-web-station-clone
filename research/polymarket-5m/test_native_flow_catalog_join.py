"""Synthetic admission and non-lookahead tests, not historical backtests."""
import copy
import unittest
from binance_native_flow import NativeFlowTape
from historical_flow_receipts import CoverageInterval
from native_flow_catalog_join import evaluate_decision, flow_candidates
from mine500_catalog import make_candidates, make_supplementary_candidates


class Tests(unittest.TestCase):
    def setUp(self):
        self.tape = NativeFlowTape(stream_kind='trade', time_unit='MILLISECOND')
        self.tape.add_trade(dict(e='trade', E=59000, T=58999, s='BTCUSDT',
                                 t=1, p='100', q='2', m=False),
                            local_recv_ms=59001, capture_run_id='epoch1')
        self.tape.add_coverage(CoverageInterval('binance','BTCUSDT','epoch1',
                              0,60000,60000,'complete','synthetic fixture'))
        self.aux = {'tau_s':self.field(120)}

    def field(self, value):
        return dict(value=value, as_of_ms=60000, available_ms=60000,
                    capture_clock_id='recorder1', provenance='synthetic fixture only')

    def run_join(self, **kw):
        args=dict(decision_recv_ms=60000,capture_clock_id='recorder1',
                  auxiliary=self.aux,split='development')
        args.update(kw)
        return evaluate_decision(self.tape,**args)

    def test_all_existing_identities_preserved(self):
        canonical={c['id']:c['rule_fingerprint'] for c in make_candidates()+make_supplementary_candidates()}
        rows=self.run_join()['rows']
        self.assertEqual(len(rows),180)
        self.assertEqual(len({r['template'] for r in rows}),18)
        self.assertTrue(all(canonical[r['candidate_id']]==r['rule_fingerprint'] for r in rows))

    def test_only_flow_only_templates_admitted_without_price(self):
        r=self.run_join()
        self.assertEqual(r['status_counts']['blocked_features'],100)
        self.assertEqual(sum(x['status']!='blocked_features' for x in r['rows']),80)
        self.assertTrue(any(x['status']=='signal' for x in r['rows']))
        tail=[x for x in r['rows'] if x['template']=='twap_tail_flow']
        self.assertTrue(all('tail_gap_bps' in x['blockers'] for x in tail))

    def test_price_confirmation_becomes_admissible_not_a_new_rule(self):
        self.aux['r_15s']=self.field(3)
        r=self.run_join()
        selected=[x for x in r['rows'] if x['template']=='flow_price_confirm_15']
        self.assertTrue(all(not x['blockers'] for x in selected))
        self.assertTrue(any(x['direction']=='Up' for x in selected))

    def test_future_availability_blocks_all_tau_rules(self):
        self.aux['tau_s']['available_ms']=60001
        self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})

    def test_stale_and_future_feature_snapshots_never_carried(self):
        for asof in (59999,60001):
            self.aux['tau_s']['as_of_ms']=asof
            self.assertEqual(self.run_join()['field_audit']['tau_s']['status'],'not_computed_for_this_decision')

    def test_clock_domain_not_interchangeable(self):
        self.aux['tau_s']['capture_clock_id']='source-event-clock'
        self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})

    def test_missing_provenance_and_invalid_number_block(self):
        self.aux['tau_s']['provenance']=' '
        self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})
        for value in (None,True,float('nan'),float('inf'),-1,301):
            self.aux['tau_s']=self.field(value)
            self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})

    def test_numeric_dict_is_not_causal_feature_evidence(self):
        self.aux['tau_s']=120
        self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})

    def test_cannot_inject_fake_native_flow(self):
        self.aux['flow_15s']=self.field(1)
        with self.assertRaises(ValueError):self.run_join()

    def test_no_coverage_stays_blocked(self):
        self.tape.engine.coverage.clear()
        self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})

    def test_unknown_native_side_cannot_be_rescued_by_price_features(self):
        self.tape.add_trade(dict(e='trade',E=59000,T=58999,s='BTCUSDT',t=2,p='100',q='2'),
                            local_recv_ms=59002,capture_run_id='epoch1')
        self.aux['r_15s']=self.field(10)
        self.aux['r_60s']=self.field(20)
        self.assertEqual(self.run_join()['status_counts'],{'blocked_features':180})

    def test_admitted_signals_exactly_match_unchanged_catalog(self):
        from mine500_catalog import signal
        r=self.run_join()
        candidates={c['id']:c for c in flow_candidates()}
        for row in r['rows']:
            if not row['blockers']:
                self.assertEqual(row['direction'],signal(candidates[row['candidate_id']],r['admitted_features']))

    def test_future_print_cannot_change_past_admission(self):
        before=self.run_join()
        self.tape.add_trade(dict(e='trade',E=59000,T=58999,s='BTCUSDT',
                                 t=2,p='100',q='20000',m=True),
                            local_recv_ms=60001,capture_run_id='epoch1')
        self.assertEqual(self.run_join(),before)

    def test_original_freeze_and_oos_refused(self):
        for split in ('oos','confirmation','test',''):
            with self.assertRaises(ValueError):self.run_join(split=split)

    def test_counts_not_backtests_and_no_mutation(self):
        before=copy.deepcopy(self.aux)
        result=self.run_join()
        self.assertEqual(self.aux,before)
        for key in ('new_candidates_registered','actual_backtests_performed','order_attempts'):
            self.assertEqual(result[key],0)
        self.assertIsNone(result['pnl_usd'])
        self.assertIsNone(result['formal_oos_result'])


if __name__=='__main__':unittest.main()
