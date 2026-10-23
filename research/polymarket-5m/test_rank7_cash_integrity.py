"""Synthetic engineering fixtures, never market backtests or profit evidence."""
from copy import deepcopy
import unittest
import replay_engine as engine
from rank7_cash_integrity import audit_cash

DAY=86400000


def signal(candidate,features):return 'Up'


def fixture(**changes):
    events=[dict(type='dataset',recv_ms=0,dataset_id='synthetic-cash-fixture',split='development',
        provenance={'source':'synthetic engineering fixture; not market data'},coverage_start_ms=0,
        coverage_end_ms=DAY+300000)]
    for day in (0,1):
        start=day*DAY;mid='synthetic-'+str(day);price=.4 if day==0 else .6
        events.append(dict(type='market',recv_ms=start,market_id=mid,start_ms=start,end_ms=start+300000,
            label_source='gamma',min_order_size=5,up_tick_size=.01,down_tick_size=.01,constraints_source='gamma'))
        for offset in (50,600,700,1100):
            events.append(dict(type='book',recv_ms=start+offset,market_id=mid,healthy=True,
                fee_rate=.07,up_asks=[[price,100]],down_asks=[[1-price,100]]))
        events.append(dict(type='decision',recv_ms=start+100,market_id=mid,
            features={'tau_s':299.9},feature_available_ms={'tau_s':start+100}))
        events.append(dict(type='outcome',recv_ms=start+300000,market_id=mid,
            resolved_ms=start+300000,winner='Up' if day==0 else 'Down',source='gamma'))
    config=engine.ReplayConfig(shares=100,initial_cash_usd=1000,require_constraints=True,**changes)
    r=engine.replay_events(sorted(events,key=lambda r:r['recv_ms']),
        [dict(id=cid,required_features=[]) for cid in ('A','B')],signal,config)
    return r['attempts'],[dict(x,report_cash_basis='engine_recorded_event') for x in r['cash_events']],r['summary']


class CashTests(unittest.TestCase):
    def setUp(self):self.a,self.e,self.s=fixture()
    def check(self):return audit_cash(self.a,self.e,self.s)
    def blocked(self):
        with self.assertRaises(ValueError):self.check()

    def test_real_engine_trace_across_dates_and_isolated_accounts(self):
        result=self.check();self.assertEqual(result['accounts'],6)
        self.assertEqual(result['submitted_orders'],12)
        self.assertTrue(all(abs(s['ending_cash_usd']-996.64)<1e-7 for s in self.s))
        self.assertIsNone(result['formal_oos_result'])

    def test_header_only_ledger_cannot_prove_fills(self):
        self.e=[];self.blocked()

    def test_midnight_reset_cannot_hide_in_positive_summary(self):
        row=next(r for r in self.a if r['candidate_id']=='A' and r['decision_ms']>=DAY)
        row['cash_before_decision']=1000.;self.blocked()

    def test_unknown_deposit_and_cross_account_event_block(self):
        original=deepcopy(self.e);self.e[0]['event_type']='deposit';self.blocked()
        self.e=original;self.e[0]['candidate_id']='B';self.blocked()

    def test_duplicate_or_reordered_sequence_blocks(self):
        self.e[1]['event_sequence']=1;self.blocked()

    def test_overborrowed_order_collateral_blocks(self):
        self.a[0]['reserved_usd_at_decision']=1001.;self.blocked()

    def test_target5_cannot_inherit_target100_config(self):
        self.a[0]['requested_shares']=5.;self.blocked()

    def test_terminal_cash_and_drawdown_are_reconstructed(self):
        original=deepcopy(self.s);self.s[0]['ending_cash_usd']+=1;self.blocked()
        self.s=original;self.s[0]['max_realized_drawdown_usd']=0;self.blocked()

    def test_partial_fak_is_valid_and_not_resized_to_full(self):
        a,e,s=fixture(depth_fraction=.5);self.assertEqual(a[0]['filled_shares'],50)
        self.assertTrue(audit_cash(a,e,s)['continuous_capital_reconstruction'])

    def test_failed_orders_release_collateral_without_profit(self):
        a,e,s=fixture(post_send_reject_bps=10000)
        self.assertTrue(all(r['filled_shares']==0 for r in a))
        self.assertTrue(audit_cash(a,e,s)['continuous_capital_reconstruction'])

    def test_delayed_payout_release_and_early_reuse(self):
        self.a,self.e,self.s=fixture(cash_release_delay_ms=60000)
        self.check();r=next(x for x in self.e if x['event_type']=='payout_release')
        r['event_ms']-=1;self.blocked()

    def test_unreleased_terminal_payout_stays_locked(self):
        a,e,s=fixture(cash_release_delay_ms=2*DAY)
        self.assertTrue(any(r['ending_locked_payout_usd']>0 for r in s))
        self.assertTrue(audit_cash(a,e,s)['continuous_capital_reconstruction'])

    def test_nonfinite_and_fallback_balances_fail_closed(self):
        original=deepcopy(self.e);self.e[0]['cash_usd']=float('nan');self.blocked()
        self.e=original;self.e[0]['report_cash_basis']='inferred';self.blocked()

    def test_missing_settlement_cannot_be_hidden_by_hash_refresh(self):
        self.e=[r for r in self.e if r['event_type']!='settlement']
        for i,r in enumerate(self.e,1):r['event_sequence']=i
        self.blocked()

    def test_ranking_amounts_and_order_counts_must_match_attempts(self):
        original=deepcopy(self.s)
        for field in ('net_pnl_usd','realized_pnl_usd','net_pnl_per_filled_share_usd',
                'attempts','submitted_orders','full_fills','partial_fills','filled_shares',
                'fees_usd','extra_cost_usd','settled_positions','unresolved_positions','cost_usd'):
            with self.subTest(field=field):
                self.s=deepcopy(original);self.s[0][field]=999
                self.blocked()
        self.s=deepcopy(original);self.s[0]['status_counts']={};self.blocked()

    def test_no_signal_and_no_fill_keep_their_distinct_null_semantics(self):
        from unittest.mock import patch
        with patch(__name__+'.signal',return_value=None):a,e,s=fixture()
        self.assertTrue(all(row['net_pnl_usd'] is None for row in s))
        self.assertTrue(audit_cash(a,e,s)['ranking_totals_independently_reconciled'])
        a,e,s=fixture(post_send_reject_bps=10000)
        self.assertTrue(all(row['net_pnl_usd']==0 for row in s))
        self.assertTrue(all(row['net_pnl_per_filled_share_usd'] is None for row in s))
        self.assertTrue(audit_cash(a,e,s)['ranking_totals_independently_reconciled'])

    def test_unfilled_fee_cannot_be_hidden_in_a_zero_cost_attempt(self):
        self.a,self.e,self.s=fixture(post_send_reject_bps=10000)
        self.a[0]['fees_usd']=.1;self.blocked()

    def test_payout_must_match_binary_winner_and_side(self):
        original=deepcopy(self.a)
        for field in ('side','winner'):
            with self.subTest(field=field):
                self.a=deepcopy(original)
                self.a[0][field]='Down' if self.a[0][field]=='Up' else 'Up'
                self.blocked()
        for field in ('side','winner'):
            with self.subTest(missing=field):
                self.a=deepcopy(original);del self.a[0][field];self.blocked()

    def test_fill_cannot_exceed_fixed_decision_limit(self):
        self.a[0]['limit_price']=.1
        with self.assertRaisesRegex(ValueError,'exceeds fixed decision limit'):self.check()

    def test_submitted_limit_requires_valid_binary_price(self):
        for limit in (None,0,1,-.1,1.1,float('nan'),True):
            with self.subTest(limit=limit):
                self.a,self.e,self.s=fixture();self.a[0]['limit_price']=limit;self.blocked()

    def test_fill_status_and_quantity_must_agree_even_when_summary_matches(self):
        from collections import Counter
        for status in ('filled_partial','no_fill'):
            with self.subTest(status=status):
                self.a,self.e,self.s=fixture();row=self.a[0];row['status']=status
                summary=next(x for x in self.s if
                    (x['candidate_id'],x['latency_ms'])==(row['candidate_id'],row['latency_ms']))
                counts=Counter(x['status'] for x in self.a if
                    (x['candidate_id'],x['latency_ms'])==(row['candidate_id'],row['latency_ms']))
                summary.update(status_counts=dict(counts),full_fills=counts['filled_full'],
                               partial_fills=counts['filled_partial'])
                self.blocked()
        self.a,self.e,self.s=fixture(depth_fraction=.5)
        self.assertTrue(self.check()['ranking_totals_independently_reconciled'])
        self.a,self.e,self.s=fixture(depth_fraction=1-5e-10)
        self.assertEqual(self.a[0]['status'],'filled_partial')
        self.assertTrue(self.check()['ranking_totals_independently_reconciled'])

    def test_immediate_or_zero_payout_cannot_be_marked_unreleased(self):
        for delay,loser_only in ((0,False),(60000,True)):
            with self.subTest(delay=delay):
                self.a,self.e,self.s=fixture(cash_release_delay_ms=delay)
                row=next(x for x in self.a if not loser_only or x['payout_usd']==0)
                row['cash_released']=False
                with self.assertRaisesRegex(ValueError,'falsely marked locked'):self.check()


if __name__=='__main__':unittest.main()
