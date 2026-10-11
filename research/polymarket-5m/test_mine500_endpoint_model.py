"""Causal training and matched-support ablation counterexamples."""
from copy import deepcopy
import pytest
import mine500_endpoint_model as m


def row():
    now=m.prior._start('2026-10-07')+1000
    f=dict(mid_up=.6,tau_s=200,ep_r_5s=1,ep_r_15s=2,ep_r_30s=3,ep_r_60s=4)
    return dict(market_id='a',recv_ms=now,market_end_ms=now+200000,
        settlement_known_ms=now+250000,target_up=1,features=f,
        feature_available_ms={k:now for k in f})


@pytest.mark.parametrize('model',m.MODELS)
def test_both_models_require_identical_causal_endpoint_support(model):
    r=row();r['features']['ep_r_60s']=None
    with pytest.raises(ValueError,match='missing'):m.vector(r,model)
    r=row();r['feature_available_ms']['ep_r_15s']=r['recv_ms']+1
    with pytest.raises(ValueError,match='future'):m.vector(r,model)


def test_control_does_not_use_endpoint_values():
    r=row();s=deepcopy(r);s['features']['ep_r_60s']=20
    assert m.vector(r,'matched_market')==m.vector(s,'matched_market')
    assert m.vector(r,'matched_endpoint')!=m.vector(s,'matched_endpoint')


@pytest.mark.parametrize('change',[
    {'settlement_known_ms':m.prior._start('2026-10-08')},
    {'market_end_ms':m.prior._start('2026-10-08')},
    {'target_up':None},
    {'recv_ms':m.prior._start('2026-10-08')+1000},
])
def test_late_label_and_same_day_not_trainable(change):
    r=row();r.update(change)
    assert not m.eligible_training(r,m.prior._start('2026-10-08'),set())


def test_market_overlap_not_trainable():
    assert not m.eligible_training(row(),m.prior._start('2026-10-08'),{'a'})
    assert m.eligible_training(row(),m.prior._start('2026-10-08'),set())


def test_score_attachment_rejects_future_fit_and_preserves_nondecision():
    r=row();r['type']='decision'
    key=('a',r['recv_ms'],'matched_market')
    scores={key:dict(p_up=.6,model_fit_ms=r['recv_ms']+1,feature_available_ms=r['recv_ms'])}
    with pytest.raises(ValueError,match='future'):list(m.attach([r],scores))
    label={'type':'outcome','recv_ms':r['recv_ms'],'market_id':'a','winner':'Up'}
    assert list(m.attach([label],{}))==[label]
