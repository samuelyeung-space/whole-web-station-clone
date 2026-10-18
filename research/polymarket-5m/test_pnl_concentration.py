"""Adversarial checks for descriptive attribution, never synthetic PnL evidence."""
import copy
import unittest
import pnl_concentration as p


def fixture(candidates=('X',)):
    # Three UTC dates, three fragments; the calendar is deliberately not 7days.
    starts=[1791417300000,1791417600000,1791504000000]
    coverage=dict(markets=[dict(market_id=str(i),start_ms=t,end_ms=t+300_000)
        for i,t in enumerate(starts)])
    metrics=[];attempts=[]
    for cid in candidates:
        for latency in p.LATENCIES:
            for scenario in p.SCENARIOS:
                metrics.append(dict(candidate_id=cid,latency_ms=latency,scenario=scenario,
                    effective_split='development',status='simulated_complete',initial_cash_usd=1000,
                    requested_shares=100,unresolved_positions=0,missing_execution_inputs=False,
                    net_pnl_usd=35,attempts=3,status_counts={'filled_full':3},filled_shares=300))
                attempts.extend(dict(candidate_id=cid,latency_ms=latency,scenario=scenario,
                    market_id=str(i),decision_ms=t+1,status='filled_full',filled_shares=100,
                    net_pnl_usd=v) for i,(t,v) in enumerate(zip(starts,[10,40,-15])))
    return dict(metrics=metrics,attempts=attempts,coverage=coverage,protocol={'source_role':p.ROLE})


class ConcentrationTests(unittest.TestCase):
    def test_attribution_uses_net_market_contributions_without_new_replay_or_confidence(self):
        result=p.analyze(**fixture());row=result['cells'][0]
        self.assertEqual(result['cell_count'],9)
        self.assertEqual(row['net_pnl_usd'],35)
        self.assertEqual(row['market']['gross_positive_contributions_usd'],50)
        self.assertEqual(row['market']['arithmetic_net_excluding_top_positive']['1'],-5)
        self.assertEqual(row['market']['positive_blocks_needed_to_erase_observed_net'],1)
        self.assertEqual(row['market']['best_positive_fraction_of_gross_positive'],.8)
        self.assertGreater(row['market']['best_positive_fraction_of_net_pnl'],1)
        self.assertEqual(result['new_replays'],0)
        self.assertIsNone(result['seven_day_pnl_usd'])
        self.assertIsNone(result['uncertainty']['confidence_interval'])
        self.assertTrue(all(not r['full_recorded_day_verified'] for r in result['daily_attribution']))

    def test_all_rules_and_scenarios_are_retained_and_duplicate_vectors_not_added(self):
        result=p.analyze(**fixture(('X','Y')))
        self.assertEqual(result['candidate_count'],2)
        self.assertEqual(result['cell_count'],18)
        self.assertEqual(len(result['main500_pairs']),3)
        self.assertTrue(all(r['exactly_equal_realised_market_vector'] for r in result['main500_pairs']))
        self.assertTrue(all(r['nonzero_pnl_market_jaccard']==1 for r in result['main500_pairs']))

    def test_missing_scenario_or_duplicate_summary_is_rejected(self):
        for duplicate in (False,True):
            data=fixture()
            if duplicate:data['metrics'].append(copy.deepcopy(data['metrics'][0]))
            else:data['metrics'].pop()
            with self.subTest(duplicate=duplicate),self.assertRaises(ValueError):p.analyze(**data)

    def test_protected_or_ambiguous_evidence_cannot_be_adopted(self):
        for role in ('untouched','OOS','not_development',None):
            data=fixture();data['protocol']['source_role']=role
            with self.subTest(role=role),self.assertRaisesRegex(ValueError,'consumed'):p.analyze(**data)

    def test_invalid_capital_or_incomplete_cells_fail_closed(self):
        cases=[('initial_cash_usd',5000),('requested_shares',5),('unresolved_positions',1),
            ('missing_execution_inputs',True),('effective_split','OOS'),('status','pending'),
            ('net_pnl_usd',float('nan'))]
        for field,value in cases:
            data=fixture();data['metrics'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):p.analyze(**data)

    def test_missing_duplicate_or_non_utc_market_calendar_is_rejected(self):
        for case in ('missing','duplicate','alignment'):
            data=fixture()
            if case=='missing':data['coverage']['markets'].pop()
            elif case=='duplicate':data['coverage']['markets'].append(data['coverage']['markets'][0])
            else:data['coverage']['markets'][0]['start_ms']+=1
            with self.subTest(case=case),self.assertRaises(ValueError):p.analyze(**data)

    def test_duplicate_unknown_and_out_of_market_attempts_fail_closed(self):
        for case in ('duplicate','unknown','time','bool_latency'):
            data=fixture()
            if case=='duplicate':data['attempts'].append(data['attempts'][0])
            elif case=='unknown':data['attempts'][0]['market_id']='unknown'
            elif case=='time':data['attempts'][0]['decision_ms']+=300_000
            else:data['attempts'][0]['latency_ms']=True
            with self.subTest(case=case),self.assertRaises(ValueError):p.analyze(**data)

    def test_unfilled_null_is_zero_attribution_only_not_a_completed_rank(self):
        data=fixture()
        for row in data['metrics']:
            row.update(attempts=4,status_counts={'filled_full':3,'no_fill':1})
            data['attempts'].append(dict(candidate_id=row['candidate_id'],latency_ms=row['latency_ms'],
                scenario=row['scenario'],market_id='0',decision_ms=1791417300002,
                status='no_fill',filled_shares=0,net_pnl_usd=None))
        result=p.analyze(**data)
        self.assertEqual(result['cells'][0]['net_pnl_usd'],35)
        self.assertIsNone(result['seven_day_rank'])

    def test_missing_settled_pnl_or_nonfinite_or_bad_quantity_is_rejected(self):
        for field,value in [('net_pnl_usd',None),('net_pnl_usd',float('inf')),
            ('filled_shares',True),('filled_shares',-1),('status','no_fill')]:
            data=fixture();data['attempts'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):p.analyze(**data)

    def test_financial_count_and_status_reconciliation_is_required(self):
        for field,value in [('net_pnl_usd',36),('filled_shares',301),('attempts',4),('status_counts',{})]:
            data=fixture();data['metrics'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):p.analyze(**data)

    def test_negative_or_zero_total_does_not_claim_erasable_profit(self):
        for total in (-10,0):
            result=p.contribution_stats([20,total-20],total)
            self.assertIsNone(result['positive_blocks_needed_to_erase_observed_net'])
            self.assertIsNone(result['best_positive_fraction_of_net_pnl'])

    def test_money_reconciliation_tolerance_does_not_change_profit_sign(self):
        result=p.contribution_stats([4e-9,7e-9,-1e-9],1e-8)
        self.assertEqual(result['positive_blocks_needed_to_erase_observed_net'],2)
        self.assertGreater(result['arithmetic_net_excluding_top_positive']['1'],0)

    def test_removing_all_positive_blocks_cannot_leave_summary_roundoff_as_profit(self):
        values=[154.81415,16.94509000000006,228.99085000000008]
        result=p.contribution_stats(values,400.75009000000045)
        self.assertEqual(result['positive_blocks_needed_to_erase_observed_net'],3)
        self.assertEqual(result['arithmetic_net_excluding_top_positive']['3'],0)
        self.assertNotEqual(result['attributed_minus_reported_net_usd'],0)

    def test_netting_inside_market_precedes_positive_contribution_measurement(self):
        data=fixture()
        for summary in data['metrics']:
            summary.update(attempts=4,status_counts={'filled_full':2,'filled_partial':2})
        extra=[]
        for row in data['attempts']:
            if row['market_id']=='0':
                row.update(filled_shares=50,status='filled_partial',net_pnl_usd=20)
                extra.append({**row,'decision_ms':row['decision_ms']+1,'net_pnl_usd':-10})
        data['attempts'].extend(extra)
        result=p.analyze(**data)
        self.assertEqual(result['cells'][0]['market']['gross_positive_contributions_usd'],50)
        self.assertEqual(result['cells'][0]['filled_markets'],3)


if __name__=='__main__':unittest.main()
