"""Original book-state and exact batch joins; no economic validation."""
import copy
from dataclasses import asdict
import json
import pytest
import h_replay_run as engine
import h_clob_update_observer as audit


class Machine:
    def __init__(self): self.events=[]
    def feed(self, row): self.events.append(row)
    def has_pending_evaluations(self): return False


def event(kind, seq=1, **changes):
    row=dict(kind=kind,seq=seq,recv_ms=1100.,source_ts_ms=1001.,asset_id='up',
             bids=[dict(price=.4,size=10)],asks=[dict(price=.6,size=10)],
             side='BUY',price=.4,size=8,best_bid=.4,best_ask=.6)
    row.update(changes)
    return row


def upstream(events, **changes):
    row=dict(event_kind='clob_batch',event_seq=events[0]['seq'],
             event_recv_ms=events[0]['recv_ms'],clob_batch_rows=len(events),audit_event_sequence=7)
    row.update(changes);return row


def run(events, connected=True, ready=True, join=True):
    observed, observer=audit.instrument(engine)
    state=[]
    for fn in [engine._apply_clob_batch,observed]:
        market=engine.DirectMarketBook('market',0,'up','down')
        if ready:
            for token in [market.up,market.down]:
                token.replace([dict(price=.4,size=10)],[dict(price=.6,size=10)],1000.)
        index={'up':(market,'Up'),'down':(market,'Down')};machine=Machine();rings={}
        result=fn(dict(events=copy.deepcopy(events),recv_ms=1100.),machine,index,rings,connected)
        state.append(json.dumps([result,asdict(market),
            {k:dict(sources=v.sources,states=[asdict(s) for s in v.states]) for k,v in rings.items()},
            machine.events],sort_keys=True))
    assert state[0]==state[1]
    rows=observer.join_batch(upstream(events)) if join else None
    if join:
        assert observer.reconcile(len(events),1)['all_individual_updates_joined_exactly_once']
        assert all(r['pnl'] is None and not r['is_order_attempt'] for r in rows)
        assert all(not isinstance(v,(dict,list)) for r in rows for v in r.values())
    return observer,rows


@pytest.mark.parametrize('ev,connected,ready,reason', [
    (event('clob_error'),True,True,'disconnect_reset'),
    (event('clob_connection'),False,True,'reconnect'),
    (event('clob_snapshot'),False,True,'disconnected'),
    (event('clob_snapshot',asset_id='other'),True,True,'unknown_asset'),
    (event('clob_snapshot'),True,False,'snapshot_applied'),
    (event('clob_snapshot',source_ts_ms=900.),True,True,'snapshot_source_regressed'),
    (event('clob_price_change'),True,False,'delta_before_ready_snapshot'),
    (event('clob_price_change',source_ts_ms=900.),True,True,'delta_source_regressed'),
    (event('clob_price_change'),True,True,'delta_applied_ready'),
    (event('clob_price_change',best_bid=.41,best_ask=.61),True,True,'delta_applied_bbo_invalidated'),
    (event('other'),True,True,'unhandled_kind'),
])
def test_original_branch_and_complete_state_equivalence(ev,connected,ready,reason):
    _,rows=run([ev],connected,ready)
    assert rows[0]['reason']==reason
    if reason=='disconnected':assert rows[0]['ready_before'] is rows[0]['ready_after'] is True


def test_batch_same_time_order_and_pre_reset_state_is_not_overwritten():
    events=[event('clob_snapshot'),event('clob_error',2),event('clob_price_change',3),
            event('clob_connection',4),event('clob_price_change',5)]
    _,rows=run(events,ready=False)
    assert [r['reason'] for r in rows]==['snapshot_applied','disconnect_reset','disconnected','reconnect','delta_before_ready_snapshot']
    assert rows[0]['ready_after'] is True and rows[2]['ready_before'] is False
    assert [r['event_seq'] for r in rows]==[1,2,3,4,5]


@pytest.mark.parametrize('changes,match', [
    (dict(clob_batch_rows=2),'count mismatch'),
    (dict(event_seq=99),'sequence mismatch'),
    (dict(event_recv_ms=99999),'clock mismatch'),
    (dict(event_kind='spot_trade'),'leaked'),
])
def test_incorrect_or_future_join_fails_closed(changes,match):
    ev=[event('clob_snapshot')];observer,_=run(ev,join=False)
    with pytest.raises(ValueError,match=match):observer.join_batch(upstream(ev,**changes))


def test_duplicate_join_and_incomplete_raw_census_fail():
    ev=[event('clob_snapshot')];observer,_=run(ev)
    with pytest.raises(ValueError,match='count mismatch'):observer.join_batch(upstream(ev))
    with pytest.raises(ValueError,match='coverage mismatch'):observer.reconcile(2,1)
    with pytest.raises(ValueError,match='coverage mismatch'):observer.reconcile(1,2)


def test_original_exception_is_preserved_and_error_ledger_not_admitted():
    fn,observer=audit.instrument(engine)
    market=engine.DirectMarketBook('m',0,'up','down')
    bad=event('clob_snapshot',bids=[dict(price='bad-price',size=1)])
    with pytest.raises(ValueError,match='bad-price'):
        fn(dict(recv_ms=1100,events=[bad]),Machine(),{'up':(market,'Up')},{},True)
    assert observer.pending[0]['reason']=='engine_exception'
    with pytest.raises(ValueError,match='incomplete/error'):observer.reconcile(1,1)


def test_original_function_and_globals_unchanged():
    original=engine._apply_clob_batch;namespace=dict(original.__globals__)
    observed,_=audit.instrument(engine)
    assert observed is not original and engine._apply_clob_batch is original
    assert original.__globals__==namespace


def test_engine_hash_drift_rejected(monkeypatch):
    monkeypatch.setattr(audit,'EXPECTED_ENGINE_SHA256','0'*64)
    with pytest.raises(ValueError,match='identity drift'):audit.instrument(engine)


def test_worker_joins_individual_rows_and_control_census(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    import h_source_race as race
    import h_replay_archive as archive
    path=Path(__file__).parent/'audits/observe_h_attempt_prefix.py'
    spec=importlib.util.spec_from_file_location('clob_worker_test',path)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    identity=dict(started_ms=1000.,ended_ms=10000.,run_id='synthetic-test')
    monkeypatch.setattr(race,'_capture_artifact_identity',lambda data:(identity,{},tmp_path))
    monkeypatch.setattr(race,'_artifact_stats',lambda data:{})
    monkeypatch.setattr(archive,'iter_market_mappings',lambda path:[dict(market_id='market',slot=0,
        up_token_id='up',down_token_id='down',fee_rate=.07)])
    clob=[event('clob_connection'),event('clob_snapshot',2),event('clob_price_change',3)]
    source=[dict(kind='spot_trade',seq=1,recv_ms=1200.,source_ts_ms=1190.,price=100.)]
    monkeypatch.setattr(archive,'iter_normalized_events',lambda path,family:iter(clob if family=='clob' else source))
    output=tmp_path/'result';output.mkdir()
    worker.worker(tmp_path,output,duration_ms=1000.,branch_opportunities=True,
                  upstream_gates=True,trigger_internals=True,clob_updates=True)
    result=json.loads((output/'result.json').read_text())
    assert result['clob_individual_update_audit']['individual_clob_rows']==3
    assert result['upstream_gate_audit']['complete_clob_individual_update_rejection_ledger']
    assert result['real_prefix_control_records_and_counters_identical']
    assert result['financial_result'] is None and not result['settlement_labels_loaded']
