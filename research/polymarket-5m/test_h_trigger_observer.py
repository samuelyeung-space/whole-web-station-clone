"""Original branch and causal-join checks; these do not test profitability."""
import copy
import math
import pytest
import h_replay_run as engine
import h_trigger_observer as audit


def run_case(*, stamp=100000, price=1.1, times=(98000, 99000),
             sigma=True, clamp=False, duplicates=True):
    observed, observer = audit.instrument(engine)
    config = engine.SignalConfig(sigma_min_observations=2)
    machines = [engine.TimestampedFirstTrigger(config), observed(config)]
    values = []
    for machine in machines:
        if sigma:
            machine.grid.count = 2
            machine.grid.total_sq = .0002
        ts = [t / 1000 for t in times]
        logs = [0.] * len(ts)
        result = machine._candidate(ts, logs, stamp, price, source="futures_book_ticker",
                                    reject_exact_duplicate=duplicates, clamp_regressed=clamp)
        values.append((result, ts, logs, vars(machine.grid)))
    assert values[0] == values[1]
    return observer, values[1][0]


@pytest.mark.parametrize("kwargs,reason", [
    ({"price": 0}, "nonpositive_price"),
    ({"price": -1}, "nonpositive_price"),
    ({"stamp": 98000}, "source_time_regressed"),
    ({"times": (98000, 100000), "price": 1}, "exact_duplicate"),
    ({"stamp": 30000, "times": ()}, "outside_signal_window"),
    ({"times": ()}, "reference_missing"),
    ({"times": (93000,)}, "reference_stale"),
    ({"sigma": False}, "sigma_not_finite"),
    ({"price": 1}, "move_below_or_equal_threshold"),
    ({}, "candidate"),
])
def test_original_return_branch_and_state_equivalence(kwargs, reason):
    observer, result = run_case(**kwargs)
    assert observer.pending["reason"] == reason
    assert (result is not None) == (reason == "candidate")
    assert all(not isinstance(v, (list, dict)) for v in observer.pending.values())


def upstream(row, **changes):
    result = {k: row[k] for k in ("source", "candidate_present", "candidate_slot",
                                  "candidate_source_ms", "candidate_direction")}
    result.update(event_source_ms=row["raw_source_ms"], audit_event_sequence=10,
                  event_kind="futures_bbo", event_seq=123, event_recv_ms=100010,
                  reason="trigger" if row["candidate_present"] else "no_candidate")
    result.update(changes)
    return result


def test_regressed_clamp_is_observed_but_never_repaired():
    observer, _ = run_case(stamp=98000, clamp=True, duplicates=False)
    assert observer.pending["source_time_clamped"] is True
    assert observer.pending["raw_source_ms"] == 98000
    assert observer.pending["effective_trigger_ms"] == 99000


def test_exact_join_consumes_once_and_matches_source_total():
    observer, _ = run_case()
    row = observer.join(upstream(observer.pending))
    assert row["audit_event_sequence"] == 10 and row["event_recv_ms"] == 100010
    assert row["financial_result"] is None and not row["is_order_attempt"]
    assert observer.reconcile({"futures_book_ticker": 1})["trigger_calls"] == 1
    with pytest.raises(ValueError, match="missing trigger"):
        observer.join(upstream(row))


@pytest.mark.parametrize("changes", [dict(source="spot_trade"), dict(event_source_ms=99999),
                                    dict(candidate_slot=999), dict(candidate_present=False)])
def test_identity_tampering_or_prefetched_future_context_is_rejected(changes):
    observer, _ = run_case()
    with pytest.raises(ValueError, match="mismatch"):
        observer.join(upstream(observer.pending, **changes))


def test_maintenance_cannot_reuse_stale_trigger():
    observer, _ = run_case()
    with pytest.raises(ValueError, match="maintenance"):
        observer.join({"source": None})
    with pytest.raises(ValueError, match="incomplete"):
        observer.reconcile({"futures_book_ticker": 1})


def test_unjoined_overwrite_and_count_mismatch_fail_closed():
    cls, observer = audit.instrument(engine)
    trigger = cls(engine.SignalConfig())
    trigger.update_source("futures_trade", 100000, 1)
    with pytest.raises(ValueError, match="unjoined"):
        trigger.update_source("futures_trade", 100001, 1)
    row = observer.pending
    observer.join(upstream(row))
    with pytest.raises(ValueError, match="count mismatch"):
        observer.reconcile({"futures_trade": 2})


def test_original_class_method_and_globals_are_unchanged():
    cls = engine.TimestampedFirstTrigger
    method = cls._candidate
    namespace = dict(method.__globals__)
    observed, _ = audit.instrument(engine)
    assert engine.TimestampedFirstTrigger is cls and cls._candidate is method
    assert method.__globals__ == namespace and observed is not cls


def test_identity_drift_rejected(monkeypatch):
    monkeypatch.setattr(audit, "EXPECTED_ENGINE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="identity drift"):
        audit.instrument(engine)
