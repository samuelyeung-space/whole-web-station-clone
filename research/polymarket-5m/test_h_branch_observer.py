"""State equivalence and branch-denominator counterexamples, never PnL tests."""
from dataclasses import asdict,replace
import pytest
import h_replay as core
import h_branch_observer as obs

def cfg(**kwargs):
    default=replace(core.ReplayConfig(),signal_time_basis='local_receipt_timestamp',
        evaluation_ms=(500.,600.,1000.),retry_after_no_send_or_kill=True,
        theta=.1)
    return replace(default,**kwargs)
def book(t=0):
    return dict(kind='snapshot',receive_ms=t,source_ms=t,market_id='m',
                up_asks=[[.4,100]],down_asks=[[.6,100]])
def sig(t,**kwargs):
    return dict(kind='signal',receive_ms=t,source_ms=t-1,market_id='m',
                direction='Up',fair=.8,signal_source='spot_trade',
                suppress_unsent_record=True,**kwargs)
def state(m):
    return (m.records,dict(m.suppressed_unsent),m._sequence,m._pending,
        {k:asdict(v) for k,v in m._markets.items()},m._sent_by_evaluation,
        m._source_watermark,m._receive_watermark)
def compare(events,config=None,force=True):
    output=[];plain=core.HReplay(config or cfg())
    observed=obs.BranchObservedHReplay(config or cfg(),sink=output.append)
    for event in events:
        plain.feed(dict(event));observed.feed(dict(event))
        assert state(plain)==state(observed)
    plain.finish(force=force);observed.finish(force=force)
    assert state(plain)==state(observed)
    branches=[r for r in output if r['kind']=='branch_opportunity']
    signals=[r for r in output if r['kind']=='eligible_signal']
    assert len(branches)==len(signals)*len(observed.config.evaluation_ms)
    assert all(r['pnl'] is None and r['financial_result'] is None for r in branches)
    return observed,branches,output

def test_pending_is_a_skip_not_a_failed_order():
    machine,rows,_=compare([book(),sig(10),sig(11),book(1300)])
    assert [r['branch_status'] for r in rows[:3]]==['scheduled_sent']*3
    assert [r['skip_reason'] for r in rows[3:]]==['pending_order']*3
    assert all(not r['is_order_attempt'] for r in rows[3:])
    assert len(machine.records)==3

def test_different_delay_states_and_censored_order_remain_distinct():
    machine,rows,_=compare([book(),sig(10),book(600),sig(700),book(1300),
                          sig(2010),book(3000)],force=False)
    second={r['evaluation_ms']:r['skip_reason'] for r in rows if r['audit_signal_sequence']==2}
    assert second=={500.:'reentry_cooldown',600.:'reentry_cooldown',1000.:'pending_order'}
    third=[r for r in rows if r['audit_signal_sequence']==3]
    assert all(r['branch_status']=='scheduled_sent' for r in third)
    assert len(machine.pending_evaluations())==1
    assert machine.pending_evaluations()[0]['evaluation_ms']==1000

def test_unsent_first_lock_is_not_pending_and_not_retried():
    low=sig(10);low.update(fair=.2,suppress_unsent_record=False)
    later=sig(20);later['suppress_unsent_record']=False
    _,rows,_=compare([book(),low,later,book(1300)],cfg(retry_after_no_send_or_kill=False))
    assert [r['branch_status'] for r in rows[:3]]==['scheduled_unsent']*3
    assert [r['skip_reason'] for r in rows[3:]]==['first_locked']*3

def test_filled_market_with_reentry_disabled_is_not_a_fill_failure():
    _,rows,_=compare([book(),sig(10),book(1300),sig(1400)],cfg(reentry_enabled=False))
    assert [r['skip_reason'] for r in rows[3:]]==['reentry_disabled']*3

def test_suppressed_decisions_link_one_to_one_without_scheduled_orders():
    low=sig(10);low['fair']=.2
    machine,rows,events=compare([book(),low])
    assert len(machine.records)==0
    assert all(r['branch_status']=='suppressed_unsent' for r in rows)
    assert len([r for r in events if r['kind']=='unsent_attempt'])==3
    assert sum(machine.suppressed_unsent.values())==3

@pytest.mark.parametrize('basis,source,expected',[
    ('local_receipt_timestamp',2000,1000),
    ('exchange_source_timestamp',2000,2000),
    ('exchange_source_timestamp',None,894),
    ('synthetic_receive_minus_lag',2000,894)])
def test_clock_basis_is_preserved_even_with_future_source_timestamp(basis,source,expected):
    signal=sig(1000);signal['source_ms']=source
    c=replace(cfg(),signal_time_basis=basis)
    _,rows,_=compare([book(),signal,book(5000)],c)
    assert all(r['signal_clock_ms']==expected for r in rows)

def test_callback_cannot_change_branch_gate_state_or_engine():
    plain=core.HReplay(cfg())
    def mutate(row):
        if row['kind']=='branch_opportunity':row['gate_state_before']['pending']='corrupt'
    observed=obs.BranchObservedHReplay(cfg(),sink=mutate)
    for event in [book(),sig(10),sig(11),book(1300)]:
        plain.feed(dict(event));observed.feed(dict(event))
    plain.finish();observed.finish();assert state(plain)==state(observed)

def test_output_failure_aborts_research_observation():
    def fail(row):
        if row['kind']=='branch_opportunity':raise OSError('trace write failed')
    m=obs.BranchObservedHReplay(cfg(),sink=fail);m.feed(book())
    with pytest.raises(OSError,match='trace write failed'):m.feed(sig(10))

def test_base_observer_drift_is_rejected(monkeypatch):
    monkeypatch.setattr(obs,'EXPECTED_BASE_SHA256','0'*64)
    with pytest.raises(ValueError,match='base attempt observer identity drift'):
        obs.BranchObservedHReplay(cfg(),sink=lambda r:None)
