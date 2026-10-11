"""Admission/leakage counterexamples; synthetic engineering checks, not PnL."""
from copy import deepcopy
import math

import unittest


def raises(exception, match):
    return unittest.TestCase().assertRaisesRegex(exception, match)

import mine500_fixed100_holdout as m


def fold():
    tree = dict(node_fields=m.FIELDS, nodes=[[.002, 100, 0, 0., 0, 0, 0, -1., 0, 1, 0, 0, 0]],
                raw_left_cat_bitsets=[], binned_left_cat_bitsets=[])
    state = dict(baseline=[[.01]], bin_thresholds=[[] for _ in range(6)], trees=[[deepcopy(tree)] for _ in range(50)])
    f = dict(model_id=m.MODEL_ID, day='2026-10-09', status='scored', learned_state=state,
             cutoff_ms=m.millis('2026-10-09T00:00:00+00:00'), train_hash='synthetic-unit-test-only',
             latest_training_label_ms=m.millis('2026-10-08T23:59:00+00:00'), training_market_ids=['training'])
    rehash(f)
    return f


def rehash(f):
    f['model_hash'] = m.execution.fingerprint([m.MODEL_ID, f['train_hash'], f['learned_state']])


def row(now=None):
    now = now if now is not None else m.millis(m.START)+100000
    features = dict(mid_up=.5, tau_s=200., ep_r_5s=1., ep_r_15s=2., ep_r_30s=3., ep_r_60s=4.)
    available = {k:now for k in features}
    return dict(type='decision', market_id='future0', recv_ms=now, features=features,
                feature_available_ms=available,
                endpoint_evidence={f'ep_r_{s}s':dict(value=features[f'ep_r_{s}s'], available_ms=now,
                    start_recv_ms=now-s*1000, end_recv_ms=now, connection_epoch=1) for s in (5,15,30,60)})


def fixture_events():
    start, end = m.millis(m.START), m.millis(m.END)
    events = [dict(type='dataset', recv_ms=start, split='holdout_candidate', dataset_id='SYNTHETIC_ENGINEERING_ONLY',
        coverage_start_ms=start, coverage_end_ms=end, observed_intervals_ms=[[start,end]],
        provenance=dict(source='synthetic_test_not_research_evidence', closed_through_ms=end,
            inputs=[dict(artifact_identity='synthetic', sha256='a'*64)],
            untouched_attestation=dict(evidence_ref='synthetic_test_does_not_certify_OOS')))]
    for i, t in enumerate(range(start, end, 300000)):
        events.append(dict(type='market', market_id=f'future{i}', recv_ms=start,
                           start_ms=t, end_ms=t+300000, label_source='gamma'))
    events.append(row())
    for i, t in enumerate(range(start, end, 300000)):
        events.append(dict(type='outcome', market_id=f'future{i}', recv_ms=end, resolved_ms=t+300000,
                           winner='Up', source='gamma'))
    return events


def protocol():
    return dict(start_ms=m.millis(m.START), end_ms=m.millis(m.END), warmup_ms=300000, expected_market_count=2016)


def test_saved_tree_sum_and_labels_cannot_influence_score():
    model = m.FrozenModel(fold()); a = row(); b = deepcopy(a)
    b.update(target_up=0, winner='Down', settlement_known_ms=0, final_price=1e100)
    assert model.predict(a) == model.predict(b)
    assert math.isclose(model.predict(a), .61, abs_tol=1e-15)


def test_control_preserves_endpoint_gate_but_not_direction_dependence():
    model = m.FrozenModel(fold()); a = row(); b = deepcopy(a)
    b['features']['ep_r_60s'] = -10000.
    assert model.predict(a) == model.predict(b)
    a['features']['ep_r_60s'] = None
    with raises(ValueError, match='missing_endpoint_support'):
        model.predict(a)


def test_predictions_reject_leakage_or_invalid_features():
    cases = [
        (lambda r:r.update(market_id='training'), 'training_market'),
        (lambda r:r.update(recv_ms=0), 'precedes_fit'),
        (lambda r:r['feature_available_ms'].update(mid_up=r['recv_ms']+1), 'future_feature'),
        (lambda r:r['features'].update(tau_s=301), 'invalid_probability'),
    ]
    for mutation, reason in cases:
        a = row(); mutation(a)
        with raises(ValueError, match=reason):
            m.FrozenModel(fold()).predict(a)


def test_model_rejects_mutated_hash_categorical_and_tree_cycle():
    f = fold(); f['learned_state']['baseline'][0][0] += 1
    with raises(ValueError, match='hash_mismatch'):m.FrozenModel(f)
    f = fold(); f['learned_state']['trees'][0][0]['nodes'][0][11] = 1; rehash(f)
    with raises(ValueError, match='categorical'):m.FrozenModel(f)
    f = fold(); node=f['learned_state']['trees'][0][0]['nodes'][0]; node[9]=0; rehash(f)
    with raises(ValueError, match='cycle'):m.FrozenModel(f)


def test_complete_synthetic_admission_is_still_not_OOS_certification():
    result = m.admit(fixture_events(), protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)
    assert result['status'] == 'admitted_for_simulation_only'
    assert result['formal_oos_result'] is None and result['simulation_result'] is None
    assert result['confirmed_strategies'] == 0


def test_old_development_cannot_be_relabelled_future_holdout():
    e = fixture_events(); e[0]['recv_ms'] = m.millis('2026-10-09T00:00:00+00:00')
    with raises(ValueError, match='old_data'):
        m.admit(e, protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)
    e = fixture_events(); e[0]['split'] = 'development'
    with raises(ValueError, match='reused_development'):
        m.admit(e, protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)


def test_missing_data_is_null_not_zero_and_missing_calendar_is_blocked():
    result = m.admit([], protocol(), m.FrozenModel(fold()), m.millis(m.START)-1)
    assert result['formal_oos_result'] is None and result['simulation_result'] is None
    assert 'holdout_window_not_closed' in result['reasons']
    e = fixture_events(); e = [x for x in e if x.get('market_id') != 'future2015']
    result = m.admit(e, protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)
    assert 'incomplete_5m_market_calendar' in result['reasons']


def test_missing_source_hash_and_declared_observation_gap_block():
    e = fixture_events(); e[0]['provenance']['inputs'][0]['sha256'] = ''
    e[0]['observed_intervals_ms'][0][0] += 1000
    r = m.admit(e, protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)
    assert 'missing_source_artifact_identity_or_sha256' in r['reasons']
    assert 'incomplete_observation_window' in r['reasons']


def test_future_endpoint_clock_and_unofficial_outcome_reject():
    e = fixture_events(); d = next(x for x in e if x['type']=='decision')
    d['endpoint_evidence']['ep_r_60s']['start_recv_ms'] = d['recv_ms']
    with raises(ValueError, match='endpoint_receipt'):
        m.admit(e, protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)
    e = fixture_events(); e[-1]['source']='binance_direction'
    with raises(ValueError, match='official_outcome'):
        m.admit(e, protocol(), m.FrozenModel(fold()), m.millis(m.END)+1000)


def test_scoring_preserves_invalid_first_signal_books_for_execution_rejection():
    d = row(); now = d['recv_ms']
    book = dict(type='book', market_id=d['market_id'], recv_ms=now-100000,
                up_asks=[[.4,100]], down_asks=[[.6,100]], healthy=False, fee_rate=.07)
    audit={}; result=list(m.scored_events([book,d], m.FrozenModel(fold()),audit))
    assert result[0] == book
    assert result[1]['features']['ask_up'] == .4
    assert m.signal(m.candidate(),result[1]['features']) == 'Up'
    # The replay engine, not feature prefiltering, must record the stale/unhealthy rejection.
    assert audit == dict(decisions=1,scored=1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith('test_') and callable(value))


if __name__ == '__main__':
    unittest.main()
