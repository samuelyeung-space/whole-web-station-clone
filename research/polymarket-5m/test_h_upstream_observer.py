"""Counter, causality and state-equivalence tests; no economic validation."""
from types import SimpleNamespace
import pytest
import h_replay_run as engine
import h_upstream_observer as audit


def event(kind="spot_trade", seq=1, recv=1000, price=2, **extra):
    return dict(kind=kind, seq=seq, recv_ms=recv, source_ts_ms=recv-1,
                price=price, bid=price, ask=price+1, **extra)


@pytest.fixture
def configured(monkeypatch):
    class Trigger:
        def __init__(self, config): pass
        def update_trade(self, stamp, price):
            return None if price == 1 else SimpleNamespace(slot=0, timestamp_s=stamp/1000, direction=1)
        update_futures = lambda self, stamp, bid, ask: self.update_trade(stamp, bid)
        update_source = lambda self, source, stamp, price: self.update_trade(stamp, price)
    class Machine:
        def __init__(self, config): self.records=[]
        def feed(self, ev):
            if ev['kind']=='signal': self.records.append(dict(ev, filled=False))
        def finish(self, force): pass
        def pending_evaluations(self): return []
    monkeypatch.setattr(engine, 'TimestampedFirstTrigger', Trigger)
    monkeypatch.setattr(engine.replay, 'HReplay', Machine)
    monkeypatch.setattr(engine, '_mapping_dict', lambda rows: ({0:SimpleNamespace(market_id='m')}, {}))
    monkeypatch.setattr(engine, '_apply_clob_batch', lambda batch,*args,**kw: batch['events'][0].get('connected',True))
    monkeypatch.setattr(engine, '_publish_market_book', lambda *args: None)
    monkeypatch.setattr(engine, '_candidate_fair', lambda *args:(.75,'trigger'))
    return monkeypatch


def compare(source, clob, *, sink=None, sources=None):
    observed=[]
    def capture(row):
        observed.append(dict(row))
        if sink: sink(row)
    fn, observer=audit.instrument(engine,capture)
    args=(list(source),list(clob),[],{},engine.replay.ReplayConfig(),engine.SignalConfig())
    expected=engine.replay_normalized(*args,trigger_sources=sources)
    actual=fn(*args,trigger_sources=sources)
    assert actual==expected
    result=observer.reconcile(actual[1],eligible_signals=len(actual[0]))
    assert all(r['pnl'] is None and not r['is_order_attempt'] for r in observed)
    return observed,result,observer,actual


def test_all_early_continues_have_one_event_outcome(configured):
    sources=[event(price=1),event(seq=2,recv=1100),event('spot_connection',3,1200),event('other',4,1300)]
    rows,result,_,_=compare(sources,[event('clob_connection',0,900)])
    assert [r['reason'] for r in rows]==['clob_batch','no_candidate','trigger','source_lifecycle','unhandled_kind']
    assert result['merged_events']==5 and result['eligible_signals']==1
    assert rows[0]['candidate_present'] is False
    assert result['complete_trigger_internal_rejection_explanation'] is False


@pytest.mark.parametrize('reason', sorted(audit.FAIR_REASONS))
def test_each_fair_gate_matches_original_counters(configured,reason):
    configured.setattr(engine,'_candidate_fair',lambda *args:(.75 if reason=='trigger' else float('nan'),reason))
    rows,result,_,_=compare([event()],[event('clob_connection',0,900)])
    assert rows[-1]['reason']==reason
    assert rows[-1]['stage']==('eligible' if reason=='trigger' else 'rejected')
    assert rows[-1]['fair']==(.75 if reason=='trigger' else None)
    assert result['upstream_counters_reconciled']


def test_disconnected_candidate_is_distinct_from_no_candidate(configured):
    rows,_,_,_=compare([event(),event(seq=2,recv=1100,price=1)],[])
    assert [r['reason'] for r in rows]==['clob_disconnected','no_candidate']


def test_missing_market_retains_candidate_identity(configured):
    configured.setattr(engine,'_mapping_dict',lambda rows:({},{}))
    rows,_,_,_=compare([event()],[event('clob_connection',0,900)])
    assert rows[-1]['reason']=='no_market' and rows[-1]['candidate_slot']==0
    assert rows[-1]['market_id'] is None


def test_disabled_source_counts_only_existing_candidates(configured):
    rows,_,observer,result=compare([event(price=1),event(seq=2,recv=1100)],[],sources=set())
    assert [r['reason'] for r in rows]==['source_disabled','source_disabled']
    assert observer.expected_counters['candidate_source_disabled_spot_trade']==1
    assert result[1]['candidate_source_disabled_spot_trade']==1


def test_prefetched_future_event_does_not_replace_current_context(configured):
    source=[event(seq=10,recv=1000),event(seq=11,recv=100000)]
    clob=[event('clob_connection',0,900),event('clob_connection',1,2000,connected=False)]
    rows,_,_,_=compare(source,clob)
    accepted=[r for r in rows if r['stage']=='eligible']
    assert [(r['event_seq'],r['event_recv_ms']) for r in accepted]==[(10,1000)]
    assert rows[-1]['event_seq']==11 and rows[-1]['reason']=='clob_disconnected'
    assert rows[2]['candidate_present'] is False  # No stale prior candidate on CLOB.


def test_counter_or_eligible_join_tampering_is_rejected(configured):
    _,_,observer,result=compare([event()],[event('clob_connection',0,900)])
    with pytest.raises(ValueError,match='counter mismatch'):
        observer.reconcile(dict(result[1],candidate_trigger=2))
    with pytest.raises(ValueError,match='signal count mismatch'):
        observer.reconcile(result[1],eligible_signals=2)
    with pytest.raises(ValueError,match='unreconciled candidate'):
        observer.reconcile(dict(result[1],candidate_unregistered=1))


def test_sink_receives_no_mutable_engine_context(configured):
    def mutate(row): row.update(reason='tampered',candidate_slot=999)
    rows,result,_,_=compare([event()],[event('clob_connection',0,900)],sink=mutate)
    assert result['eligible_signals']==1 and rows[-1]['candidate_slot']==0


def test_sink_failure_aborts_audit(configured):
    def fail(row): raise OSError('trace unavailable')
    fn,_=audit.instrument(engine,fail)
    with pytest.raises(OSError,match='trace unavailable'):
        fn([event()],[],[],{},engine.replay.ReplayConfig(),engine.SignalConfig())


def test_engine_exception_is_preserved_and_incomplete_ledger_rejected(configured):
    def fail(*args): raise RuntimeError('fair failure')
    configured.setattr(engine,'_candidate_fair',fail)
    rows=[];fn,observer=audit.instrument(engine,rows.append)
    with pytest.raises(RuntimeError,match='fair failure'):
        fn([event()],[event('clob_connection',0,900)],[],{},engine.replay.ReplayConfig(),engine.SignalConfig())
    assert rows[-1]['reason']=='engine_exception'
    with pytest.raises(ValueError,match='engine exception'):observer.reconcile({})


def test_engine_identity_drift_is_rejected(monkeypatch):
    monkeypatch.setattr(audit,'EXPECTED_ENGINE_SHA256','0'*64)
    with pytest.raises(ValueError,match='identity drift'):audit.instrument(engine,lambda row:None)
