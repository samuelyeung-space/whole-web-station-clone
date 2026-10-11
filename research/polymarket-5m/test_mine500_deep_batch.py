"""Batch orchestration counterexamples: no profitability assertions."""
import json
from pathlib import Path
import pytest
import mine500_deep_batch as batch

DAY = 1791331200000  # 2026-10-07 UTC


def tape(tmp_path, name, start=DAY, end=DAY+2000, records=()):
    header = dict(type='dataset', recv_ms=start, dataset_id=name, split='development',
                  coverage_start_ms=start, coverage_end_ms=end,
                  book_resolution='decision_and_match_projection',
                  supported_latencies_ms=[500,600,1000], decision_grid_ms=1000)
    path=tmp_path/name
    path.write_text('\n'.join(json.dumps(row) for row in [header,*records])+'\n')
    return path


def test_disjoint_merge_retains_late_label_receipt_and_gaps(tmp_path):
    label=dict(type='outcome',market_id='m',recv_ms=DAY+12000,resolved_ms=DAY+1000,winner='Up',source='gamma')
    first=tape(tmp_path,'a',records=[dict(type='book',market_id='m',recv_ms=DAY+1000),label])
    second=tape(tmp_path,'b',DAY+10000,DAY+11000,
                [dict(type='book',market_id='n',recv_ms=DAY+10500)])
    events,audit=batch.combine_inputs([second,first])
    assert events[-1]==label
    assert events[0]['observed_intervals_ms']==[(DAY,DAY+2000),(DAY+10000,DAY+11000)]
    assert events[0]['observed_hours']==pytest.approx(3000/3600000)
    assert [r['recv_ms'] for r in events[1:]]==[DAY+1000,DAY+10500,DAY+12000]
    assert audit['record_counts']['outcome']==1


def test_overlap_not_deduplicated_silently(tmp_path):
    a=tape(tmp_path,'a');b=tape(tmp_path,'b',DAY+1000,DAY+3000)
    with pytest.raises(ValueError,match='overlapping'):batch.combine_inputs([a,b])


def test_duplicate_decision_rejected(tmp_path):
    d=dict(type='decision',market_id='m',recv_ms=DAY+1000)
    a=tape(tmp_path,'a',records=[d,d])
    with pytest.raises(ValueError,match='duplicate decision'):batch.combine_inputs([a])


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True])
def test_nonfinite_or_boolean_header_clock_rejected(tmp_path,bad):
    a=tape(tmp_path,'a',start=bad)
    with pytest.raises(ValueError,match='invalid input interval'):batch.combine_inputs([a])


def test_own_interval_and_individual_stream_sort_enforced(tmp_path):
    a=tape(tmp_path,'a',records=[dict(type='decision',market_id='m',recv_ms=DAY+3000)])
    with pytest.raises(ValueError,match='own recording'):batch.combine_inputs([a])
    a=tape(tmp_path,'a',records=[dict(type='book',recv_ms=DAY+1500),dict(type='book',recv_ms=DAY+1000)])
    with pytest.raises(ValueError,match='regressed'):batch.combine_inputs([a])


def test_unconsumed_day_rejected(tmp_path):
    oct12=DAY+5*86400000
    a=tape(tmp_path,'a',oct12,oct12+2000,[dict(type='decision',market_id='m',recv_ms=oct12+1000)])
    with pytest.raises(ValueError,match='unconsumed'):batch.combine_inputs([a])


def attempt(status='no_fill',**updates):
    row=dict(candidate_id='PATH-001',latency_ms=500,market_id='m',decision_ms=DAY,
        match_ms=DAY+500,side='Up',requested_shares=5,submitted=True,status=status,
        filled_shares=0,cost_usd=0,fees_usd=0,reserved_usd_at_decision=2.5,
        settled=False,payout_usd=None,settlement_ms=None,net_pnl_usd=None)
    row.update(updates);return row


@pytest.mark.parametrize('status',['incomplete_match_horizon','rejected_missing_book_at_match',
    'rejected_missing_required_constraints_at_match','rejected_fee_changed_after_decision'])
def test_incomplete_zero_fill_never_becomes_complete_panel_zero(status):
    doc=dict(config={'latencies_ms':[500]},attempts=[attempt(status)])
    result=batch.panel_results(doc)[0]
    assert result['status']=='blocked_incomplete_panel_execution'
    assert result['net_pnl_usd'] is None
    assert result['chosen_attempts'][0]['status']==status


def test_unresolved_chosen_position_preserved(tmp_path):
    doc=dict(config={'latencies_ms':[500]},attempts=[attempt('filled_full',filled_shares=5,cost_usd=2.5)])
    result=batch.panel_results(doc)[0]
    assert result['status']=='blocked_unresolved_panel_positions'
    assert result['net_pnl_usd'] is None
    assert len(result['chosen_attempts'])==1


def test_failed_first_signal_not_replaced_by_later_winner():
    failed=attempt('no_fill',net_pnl_usd=0)
    winner=attempt('filled_full',candidate_id='MPR-any',decision_ms=DAY+1000,
        match_ms=DAY+1500,filled_shares=5,cost_usd=2.5,settled=True,
        settlement_ms=DAY+300000,payout_usd=5,net_pnl_usd=2.5)
    result=batch.panel_results(dict(config={'latencies_ms':[500]},attempts=[winner,failed]))[0]
    assert result['net_pnl_usd']==0
    assert result['chosen_attempts'][0]['candidate_id']=='PATH-001'
    assert result['conflicts'][0]['suppressed_signals']==1


def test_registration_precedes_any_model_fit_or_result(tmp_path,monkeypatch):
    source=tape(tmp_path,'a')
    out=tmp_path/'out'
    class StopAfterRegistration(Exception):pass
    def fit(*args,**kwargs):
        registration=json.loads((out/'registration.json').read_text())
        assert len(registration['candidates'])==registration['new_trials']==40
        assert registration['formal_oos_results'] is None
        assert not (out/'new_candidate_metrics.json').exists()
        raise StopAfterRegistration
    monkeypatch.setattr(batch.probability,'train_forward_days',fit)
    with pytest.raises(StopAfterRegistration):batch.run([source],out,include_reference=False)
    assert (out/'registration.json').exists()
    with pytest.raises(FileExistsError):batch.run([source],out,include_reference=False)


def test_v2_statistics_and_panel_end_to_end_synthetic_only(tmp_path):
    def book(t):
        return dict(type='book',market_id='m',recv_ms=t,up_recv_ms=t,down_recv_ms=t,
                    healthy=True,up_asks=[[.4,10]],down_asks=[[.7,10]],fee_rate=.07)
    records=[dict(type='market',market_id='m',recv_ms=DAY,start_ms=DAY,end_ms=DAY+300000,label_source='gamma'),
             book(DAY+1000),dict(type='decision',market_id='m',recv_ms=DAY+1000,
                 features={'mid_up':.6,'tau_s':299},feature_available_ms={'mid_up':DAY+1000,'tau_s':DAY+1000}),
             book(DAY+1500),book(DAY+1600),book(DAY+2000),
             dict(type='outcome',market_id='m',recv_ms=DAY+310000,resolved_ms=DAY+300000,
                  winner='Up',source='gamma')]
    source=tape(tmp_path,'a',end=DAY+2500,records=records)
    out=tmp_path/'run'
    batch.run([source],out,include_reference=False)
    done=json.loads((out/'completion.json').read_text())
    assert done['new_candidate_scenarios']==40*3*8
    assert done['formal_oos_result'] is None
    result=json.loads((out/'size5_replay.json').read_text())
    stats=json.loads((out/'size5_statistics.json').read_text())
    by_key={(r['candidate_id'],r['latency_ms']):r for r in result['summary']}
    for row in stats['results']:
        assert row['net_pnl_usd']==pytest.approx(by_key[row['candidate_id'],row['latency_ms']]['net_pnl_usd'])
        assert row['qualified'] is False
    assert stats['results']
