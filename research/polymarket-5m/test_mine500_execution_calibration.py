"""Regression counterexamples for receipt calibration, never market backtests."""
from dataclasses import replace
import pytest
import mine500_execution_calibration as cal


def row(identifier='a',**updates):
    value=dict(attempt_id=identifier,market_id='m-'+identifier,decision_ms=0,send_ms=100,
        match_ms=500,response_ms=550,requested_shares=5,paper_fill_shares=5,
        actual_fill_shares=5,actual_status='filled_full',receipt_source='private_order_response',
        limit_price=.65,paper_fill_vwap=.4,paper_fees_usd=.084,actual_fill_vwap=.6,
        actual_fees_usd=.084,settlement_payout_per_share=1,venue_hold_ms=150,
        match_time_source='measured_clock_aligned_venue',match_clock_error_bound_ms=1)
    value.update(updates); return value


def no_match(identifier,status):
    return row(identifier,actual_status=status,actual_fill_shares=None if status=='unknown' else 0,
        actual_fill_vwap=None,actual_fees_usd=None,match_ms=None,
        match_time_source=None,match_clock_error_bound_ms=None,response_ms=None if status=='unknown' else 550)


def test_actual_economics_use_actual_price_and_fees_not_paper_price():
    result=cal.calibrate([cal.parse_attempt(row())])
    assert result['economics']['paper_pnl_usd']==pytest.approx(2.916)
    assert result['economics']['actual_pnl_usd']==pytest.approx(1.916)
    assert result['adverse_selection']['actual_pnl_usd']==pytest.approx(1.916)


def test_partial_actual_pnl_uses_actual_quantity_and_total_actual_fee():
    a=cal.parse_attempt(row(actual_status='filled_partial',actual_fill_shares=2,actual_fees_usd=.031))
    assert cal.calibrate([a])['economics']['actual_pnl_usd']==pytest.approx(.769)


def test_v1_shared_pnl_is_explicitly_invalid():
    with pytest.raises(ValueError,match='invalid_for_evidence'):
        cal.parse_attempt(row(net_pnl_per_share=.4))


@pytest.mark.parametrize('status',['unknown','killed','rejected'])
def test_no_trade_match_time_preserves_failed_unknown_denominators(status):
    result=cal.calibrate([cal.parse_attempt(no_match('x',status))])
    assert result['attempts']==1
    assert result['paper_full_predictions_all']==1
    assert result['latency_ms']['decision_to_match_p50'] is None
    assert result['by_total_decision_to_match_bucket']['unobserved_match_clock']['attempts']==1
    assert result['economics']['actual_pnl_usd']==(None if status=='unknown' else 0)


def test_unknown_not_imputed_zero_and_selection_bounds_include_it():
    result=cal.calibrate([cal.parse_attempt(row()),cal.parse_attempt(no_match('u','unknown'))])
    assert result['known_only_realization_rate']==1
    assert result['all_prediction_missing_outcome_bounds']==[.5,1]
    assert result['paper_full_realization_cluster_99pct_interval']['status']=='unresolved_outcomes'
    with pytest.raises(ValueError,match='must be null'):
        cal.parse_attempt({**no_match('u','unknown'),'actual_fill_shares':0})


def test_replicating_one_market_never_creates_precise_ci():
    rows=[cal.parse_attempt(row(str(i),market_id='same')) for i in range(1000)]
    result=cal.calibrate(rows)
    assert result['unique_markets']==1
    assert result['paper_full_realization_exact_99pct_interval'] is None
    assert result['paper_full_realization_cluster_99pct_interval']['interval'] is None
    assert result['adverse_selection']['one_sided_fisher_p_value'] is None


def test_whole_day_clusters_prevent_within_day_duplication_precision():
    rows=[]
    for day in range(7):
        for m in range(3):
            for order in range(10):
                t=day*86400000
                data=row(f'{day}-{m}-{order}',market_id=f'{day}-{m}',decision_ms=t,send_ms=t+100,
                         match_ms=t+500,response_ms=t+550)
                if day%2:
                    data.update(actual_status='killed',actual_fill_shares=0,actual_fill_vwap=None,
                        actual_fees_usd=None,match_ms=None,match_time_source=None,match_clock_error_bound_ms=None)
                rows.append(cal.parse_attempt(data))
    full=cal.calibrate(rows,bootstrap_reps=500)
    reduced=cal.calibrate(rows[::10],bootstrap_reps=500)
    assert full['paper_full_realization_cluster_99pct_interval']==reduced['paper_full_realization_cluster_99pct_interval']
    assert full['paper_full_realization_cluster_99pct_interval']['status']=='descriptive_only'


def test_missing_fill_economics_stays_null_not_zero():
    result=cal.calibrate([cal.parse_attempt(row(actual_fill_vwap=None,actual_fees_usd=None))])
    assert result['economics']['actual_pnl_usd'] is None
    assert result['economics']['actual_economics_missing_rows']==1


def test_match_hold_is_not_added_twice_and_match_provenance_is_required():
    result=cal.calibrate([cal.parse_attempt(row())])
    assert result['latency_ms']['decision_to_match_p50']==500
    assert result['latency_ms']['send_to_match_p50']==400
    with pytest.raises(ValueError,match='provenance'):
        cal.parse_attempt(row(match_time_source='local_response_timestamp'))
    with pytest.raises(ValueError,match='shorter than venue_hold'):
        cal.parse_attempt(row(match_ms=200))


@pytest.mark.parametrize('updates',[
    {'limit_price':.5}, {'actual_fill_vwap':.7}, {'actual_fees_usd':-1},
    {'actual_status':'filled_partial'}, {'send_ms':600}, {'paper_fill_shares':True},
    {'settlement_payout_per_share':.5}, {'receipt_source':'paper'},
    {'market_id':None}, {'match_clock_error_bound_ms':None},
])
def test_invalid_inputs_fail_closed(updates):
    with pytest.raises(ValueError):cal.parse_attempt(row(**updates))


def test_programmatic_dataclass_calls_are_revalidated_and_ids_unique():
    attempt=cal.parse_attempt(row())
    with pytest.raises(ValueError,match='unique'):cal.calibrate([attempt,attempt])
    with pytest.raises(ValueError,match='final outcome'):cal.calibrate([replace(attempt,actual_fill_shares=100)])


def test_empty_input_is_not_a_zero_pnl_strategy_result():
    result=cal.calibrate([])
    assert result['economics']['actual_pnl_usd'] is None
    assert result['qualification']['strategy_result'] is False
