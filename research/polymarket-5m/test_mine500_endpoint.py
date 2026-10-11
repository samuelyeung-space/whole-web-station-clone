"""Causal counterexamples, not strategy-profit evidence."""
import math
import pytest
from mine500_endpoint import EndpointPrices, augment, candidates, signal


def event(now, kind='spot_trade', seq=1, epoch=1, price=100):
    return dict(recv_ms=now, kind=kind, stream_sequence=seq,
                connection_epoch=epoch, price=price, source_ts_ms=999999)


def connected():
    p = EndpointPrices(); p.consume(event(0, 'spot_connection', 0)); return p


def test_endpoint_does_not_require_intermediate_fresh_path():
    p = connected(); p.consume(event(1000)); p.consume(event(61000, seq=2, price=101))
    assert p.at(61000,60)['value'] == pytest.approx(10000*math.log(1.01))
    assert p.at(61000,15) is None


def test_endpoint_age_and_future_boundary():
    p=connected();p.consume(event(1000));p.consume(event(61000,seq=2,price=101))
    assert p.at(62000,60) is not None
    assert p.at(62001,60) is None
    with pytest.raises(ValueError,match='past decision'): p.at(60999,60)


def test_asof_target_never_uses_later_trade():
    p=connected();p.consume(event(1000));p.consume(event(1100,seq=2,price=999));p.consume(event(61000,seq=3,price=101))
    assert p.at(61000,60)['value'] == pytest.approx(10000*math.log(1.01))


def test_disconnect_reconnect_invalidates_old_endpoint():
    p=connected();p.consume(event(1000));p.consume(event(2000,'spot_disconnect',2))
    p.consume(event(60000,'spot_connection',0,2));p.consume(event(61000,seq=1,epoch=2))
    assert p.at(61000,60) is None


def test_bbo_consumes_sequence_without_substituting_trade_price():
    p=connected();p.consume(event(1000));p.consume(event(60000,'spot_bbo',2,price=999))
    p.consume(event(61000,seq=3,price=101))
    assert p.at(61000,60)['value']==pytest.approx(10000*math.log(1.01))


@pytest.mark.parametrize('bad',[event(1000,seq=2),event(1000,epoch=2)])
def test_sequence_and_epoch_fail_closed(bad):
    with pytest.raises(ValueError,match='gap or epoch'): connected().consume(bad)


def test_future_source_not_consumed_and_original_nulls_preserved():
    d=dict(type='decision',recv_ms=61000,features={'r_60s':None,'rv_60s':None},feature_available_ms={})
    source=[event(0,'spot_connection',0),event(1000),event(61000,seq=2,price=101),event(61001,seq=3,price=900)]
    out=list(augment([d],source,{}))[0]
    assert out['features']['ep_r_60s']==pytest.approx(10000*math.log(1.01))
    assert out['features']['r_60s'] is out['features']['rv_60s'] is None
    assert 'ep_r_60s' not in d['features']
    assert out['endpoint_evidence']['ep_r_60s']['end_recv_ms']==61000


def test_future_corruption_aborts_not_retroactive_health_selection():
    d=dict(type='decision',recv_ms=61000,features={},feature_available_ms={})
    it=augment([d],[event(0,'spot_connection',0),event(1000),event(61000,seq=2),event(62000,seq=4)],{})
    assert next(it)['features']['ep_r_60s']==0
    with pytest.raises(ValueError,match='gap'): next(it)


def test_registry_unique_rules_and_mirror():
    rows=candidates();assert len(rows)==24
    assert len({r['rule_fingerprint'] for r in rows})==24
    for c in rows:
        f={k:3 for k in c['required_features']};f['tau_s']=c['params']['tau_lo']
        side=signal(c,f)
        for key in f:
            if key!='tau_s': f[key]*=-1
        assert signal(c,f)==({'Up':'Down','Down':'Up'}[side])
