"""Engineering invariants; synthetic paths are not financial backtests."""
from dataclasses import replace,asdict
import pytest
import h_replay as core
from h_attempt_observer import ObservedHReplay,verify_engine

def config(**kw):
    return replace(core.ReplayConfig(),signal_time_basis='local_receipt_timestamp',
        evaluation_ms=(500.0,600.0,1000.0),retry_after_no_send_or_kill=True,
        theta=0.1,**kw)

def snapshot(t=0,ask=.4):
    return dict(kind='snapshot',receive_ms=t,source_ms=t,market_id='m',
        up_asks=[[ask,100]],down_asks=[[.6,100]])
def signal(t,fair=.8,**kw):
    return dict(kind='signal',receive_ms=t,source_ms=t-1,market_id='m',
        direction='Up',fair=fair,signal_source='spot_trade',
        suppress_unsent_record=True,**kw)

def state(machine):
    return (machine.records,dict(machine.suppressed_unsent),machine._sequence,
        {k:asdict(v) for k,v in machine._markets.items()},machine._sent_by_evaluation)

def compare(events,cfg=None):
    rows=[];a=core.HReplay(cfg or config());b=ObservedHReplay(cfg or config(),sink=rows.append)
    for event in events:a.feed(dict(event));b.feed(dict(event))
    a.finish();b.finish()
    assert state(a)==state(b)
    return a,b,rows

def test_suppressed_attempts_are_observed_without_changing_state():
    a,b,rows=compare([snapshot(),signal(10,fair=.2),signal(20),snapshot(1300)])
    unsent=[r for r in rows if r['kind']=='unsent_attempt']
    assert len(unsent)==3
    assert {r['evaluation_ms'] for r in unsent}=={500,600,1000}
    assert all(r['reason']=='ask_above_limit' and r['pnl'] is None for r in unsent)
    assert len([r for r in rows if r['kind']=='evaluation'])==3
    assert {r['audit_signal_sequence'] for r in rows if r['kind']=='evaluation'}=={2}

def test_rate_block_and_missing_book_have_explicit_reasons():
    a,b,rows=compare([signal(1),snapshot(2),signal(10),snapshot(1300),signal(2500)],
        config(max_orders_per_min=1))
    reasons={r['reason'] for r in rows if r['kind']=='unsent_attempt'}
    assert reasons=={'book_unavailable_at_decision','rate'}

def test_pending_branches_are_signals_but_not_invented_attempts():
    a,b,rows=compare([snapshot(),signal(10),signal(11),snapshot(1300)])
    assert len([r for r in rows if r['kind']=='eligible_signal'])==2
    assert len([r for r in rows if r['kind']=='evaluation'])==3
    assert not [r for r in rows if r['kind']=='unsent_attempt']

def test_sink_failure_stops_audit():
    def fail(_):raise OSError('disk full')
    machine=ObservedHReplay(config(),sink=fail)
    machine.feed(snapshot())
    with pytest.raises(OSError,match='disk full'):machine.feed(signal(1))

def test_engine_identity_is_verified():
    assert verify_engine().startswith('38bf2d')

def test_sink_cannot_mutate_engine_evaluations():
    def mutate(row):
        if row['kind']=='evaluation':row['evaluation']['filled_shares']=-999
    a=core.HReplay(config());b=ObservedHReplay(config(),sink=mutate)
    for event in [snapshot(),signal(10),snapshot(1300)]:
        a.feed(dict(event));b.feed(dict(event))
    a.finish();b.finish()
    assert state(a)==state(b)
