"""Synthetic causal and specification checks, never backtest evidence."""
import copy

import numpy as np
import pytest

import mine500_tree_models as tree
from test_mine500_probability_models import tape, features, ms


def train(events, days=('2026-10-07','2026-10-08')):
    return tree.train_forward_days(events,consumed_days=days,input_hashes={'synthetic':'a'*64})


@pytest.fixture(scope='module')
def forward():
    rows=tape('2026-10-07',count=40)+tape('2026-10-08',count=40)
    return rows,*train(rows)


def training_rows(count=40, repetitions=1):
    rows=[]
    for i in range(count):
        f=features(.35 if i%2 else .65)
        t=ms('2026-10-07')+i*300000+60000
        row=dict(market_id=str(i),recv_ms=t,features=f,feature_available_ms=dict.fromkeys(f,t),
                 target_up=i%2,settlement_known_ms=t+240000,market_end_ms=t+240000)
        rows.extend([copy.deepcopy(row) for _ in range(repetitions)])
    return rows


def test_registry_exact_twelve_trials_fixed_model_specifications():
    candidates=tree.make_candidates()
    assert len(candidates)==len({c['id'] for c in candidates})==12
    assert all(c['id'].startswith('MPT-') for c in candidates)
    assert {c['margin'] for c in candidates}=={.02,.04}
    assert {c['max_leaf_nodes'] for c in candidates}=={4,8}
    assert tree.FIXED_PARAMETERS['early_stopping'] is False
    assert tree.FIXED_PARAMETERS['validation_fraction'] is None
    assert tree.FIXED_PARAMETERS['max_iter']==50
    assert tree.FIXED_PARAMETERS['l2_regularization']==10
    assert tree.FIXED_PARAMETERS['min_samples_leaf']==100


def test_forward_cutoff_market_isolation_and_blocked_first_day(forward):
    _,audit,scores=forward
    assert len(scores)==40*6
    for fold in audit['folds']:
        if fold['day_utc']=='2026-10-07':
            assert fold['status']=='blocked' and fold['fitted'] is None
            continue
        assert fold['status']=='scored'
        assert fold['latest_training_label_known_ms']<fold['fit_cutoff_ms']
        assert all(mid.startswith('2026-10-07') for mid in fold['fitted']['training_market_ids'])
        assert fold['fitted']['n_iter']==50
        assert set(fold['fitted']['training_weight_sums_by_market'].values())=={1.}
    assert audit['formal_oos_result'] is None and audit['confirmed_strategies']==0
    assert audit['independent_economic_mechanisms_claimed']==0


def test_prediction_day_outcomes_never_change_model_or_scores(forward):
    rows,audit,scores=forward
    mutated=copy.deepcopy(rows)
    for row in mutated:
        if row['type']=='outcome' and row['market_id'].startswith('2026-10-08'):
            row['winner']='Down' if row['winner']=='Up' else 'Up'
    after,new_scores=train(mutated)
    assert scores==new_scores
    assert [f['fitted'] for f in audit['folds']]==[f['fitted'] for f in after['folds']]


def test_late_official_label_receipt_blocks_all_models():
    audit,scores=train(tape('2026-10-07',count=40,outcome_delay=3*86400000)+tape('2026-10-08',count=40))
    assert not scores
    assert all(f['status']=='blocked' and f['metrics'] is None for f in audit['folds'])


def test_future_feature_unavailable_only_to_dependent_groups(forward):
    rows,_,_=forward
    mutated=copy.deepcopy(rows)
    decision=next(r for r in mutated if r['type']=='decision' and r['market_id'].startswith('2026-10-08'))
    decision['feature_available_ms']['r_5s']=decision['recv_ms']+1
    _,scores=train(mutated)
    for model in tree.MODEL_IDS:
        key=decision['market_id'],decision['recv_ms'],model
        assert (key in scores)==model.startswith('market_calibration')


def test_minimum_unique_markets_and_per_class_fail_closed():
    with pytest.raises(ValueError,match='insufficient_training_markets'):
        tree.fit_residual(training_rows(39,20),tree.MODEL_IDS[0],ms('2026-10-08'))
    rows=training_rows()
    for i,row in enumerate(rows):
        row['target_up']=int(i<9)
    with pytest.raises(ValueError,match='insufficient_training_class_markets'):
        tree.fit_residual(rows,tree.MODEL_IDS[0],ms('2026-10-08'))


def test_market_weight_one_despite_row_density_and_no_training_market_prediction():
    rows=training_rows()+[copy.deepcopy(training_rows()[0]) for _ in range(199)]
    model=tree.fit_residual(rows,tree.MODEL_IDS[0],ms('2026-10-08'))
    assert all(v==1 for v in model.training_weight_sums.values())
    with pytest.raises(ValueError,match='training_market'):
        model.predict(dict(rows[0],recv_ms=ms('2026-10-08')+1))
    with pytest.raises(ValueError,match='precedes_fit'):
        model.predict(dict(rows[0],market_id='new'))


def test_tree_is_fitted_residual_with_real_nonlinear_splits_and_bounded_prediction():
    rows=training_rows(40,10)
    for row in rows:
        i=int(row['market_id'])
        row['features']['mid_up']=.5
        row['features']['r_60s']=-12 if i<10 else (0 if i<30 else 12)
        row['target_up']=int(i<10 or i>=30)
    model=tree.fit_residual(rows,'external_path_L4',ms('2026-10-08'))
    assert any(len(stage[0].nodes)>1 for stage in model.estimator._predictors)
    test=[]
    for i,r60 in enumerate((-12,0,12)):
        row=copy.deepcopy(rows[0])
        row.update(market_id='unseen-'+str(i),recv_ms=ms('2026-10-08')+1)
        row['features']['r_60s']=r60
        test.append(row)
    p=model.predict_many(test)
    assert p[0]>p[1] and p[2]>p[1]
    assert all(.001<=v<=.999 for v in p)
    desc=model.as_dict()
    assert len(desc['model_hash'])==64 and desc['learned_state']['trees']


@pytest.mark.parametrize('field,value', [('settlement_known_ms',ms('2026-10-08')),
    ('market_end_ms',ms('2026-10-08')),('recv_ms',float('nan'))])
def test_direct_fit_cannot_bypass_training_clocks(field,value):
    rows=training_rows()
    rows[0][field]=value
    with pytest.raises(ValueError,match='clock|not_available'):
        tree.fit_residual(rows,tree.MODEL_IDS[0],ms('2026-10-08'))


def test_signal_uses_asks_and_fees_and_missing_predictions_do_not_signal():
    candidate=tree.make_candidates()[0]
    key='tree_prob_'+candidate['model_id']
    f={key:.55,'ask_up':.54,'ask_down':.48,'decision_fee_rate':.07}
    assert tree.signal(candidate,f) is None
    f['ask_up']=.50
    assert tree.signal(candidate,f)=='Up'
    f[key]=None
    assert tree.signal(candidate,f) is None


def test_attachment_exact_join_and_future_score_failclosed():
    decision={'type':'decision','recv_ms':100,'market_id':'m','features':{},'feature_available_ms':{}}
    book={'type':'book','recv_ms':99,'market_id':'m','up_asks':[[.51,5]],'down_asks':[[.50,5]],'fee_rate':.07,'healthy':False}
    candidate=tree.make_candidates()[0]
    score={('m',100,candidate['model_id']):{'p_up':.60,'model_fit_ms':0,'feature_available_ms':98}}
    row=list(tree.attach_scores([book,decision],score))[-1]
    assert tree.signal(candidate,row['features'])=='Up'
    assert decision['features']=={}
    score[('m',100,candidate['model_id'])]['model_fit_ms']=101
    with pytest.raises(ValueError,match='future_model'):
        list(tree.attach_scores([decision],score))
    assert list(tree.attach_scores([decision],{}))[0]['features']['tree_prob_'+candidate['model_id']] is None


def test_undeclared_dates_split_and_duplicate_conflicts_fail_closed():
    with pytest.raises(ValueError,match='declared_consumed'):
        train(tape('2026-10-10'),days=('2026-10-10',))
    rows=tape('2026-10-07',count=40)
    rows[0]['split']='oos'
    with pytest.raises(ValueError,match='development'):
        train(rows)
    rows=tape('2026-10-07',count=40)
    dup=copy.deepcopy(next(r for r in rows if r['type']=='decision'))
    dup['features']['mid_up']+=.01
    with pytest.raises(ValueError,match='conflicting_duplicate'):
        train(rows+[dup])
