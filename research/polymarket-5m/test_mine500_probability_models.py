"""Synthetic causal/leakage checks; no fixture constitutes market evidence."""
import copy
from datetime import datetime, timezone

import numpy as np
import pytest

from mine500_probability_models import (MODEL_IDS, attach_scores, feature_vector,
    fit_residual, make_candidates, signal, train_forward_days)


def ms(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)


def features(p=.5):
    return {"mid_up":p,"tau_s":240.,"r_5s":1.,"r_15s":2.,"r_30s":3.,"r_60s":4.,
            "rv_15s":2.,"rv_60s":4.,"pm_r_5s":.01,"pm_r_15s":.02,"imbalance_up":.1}


def tape(day, count=24, *, outcome_delay=0):
    start=ms(day)
    rows=[{"type":"dataset","recv_ms":start,"split":"development"}]
    for i in range(count):
        t=start+i*300000
        mid=f"{day}-{i}"
        f=features(.35 if i%2 else .65)
        rows += [{"type":"market","market_id":mid,"recv_ms":t,"start_ms":t,
                  "end_ms":t+300000,"label_source":"gamma"},
                 {"type":"book","market_id":mid,"recv_ms":t+60000,"up_asks":[[.52,10]],
                  "down_asks":[[.49,10]],"fee_rate":.07,"healthy":True},
                 {"type":"decision","market_id":mid,"recv_ms":t+60000,"features":f,
                  "feature_available_ms":dict.fromkeys(f,t+60000)},
                 {"type":"outcome","market_id":mid,"recv_ms":t+300000+outcome_delay,
                  "resolved_ms":t+300000,"winner":"Up" if i%2 else "Down","source":"gamma"}]
    return sorted(rows,key=lambda x:x["recv_ms"])


def train(events, days=("2026-10-07","2026-10-08")):
    return train_forward_days(events,consumed_days=days,input_hashes={"synthetic":"a"*64})


def test_registry_exactly_eight_trials_not_independent_mechanisms():
    c=make_candidates()
    assert len(c)==8 and len({r["id"] for r in c})==8
    assert {r["margin"] for r in c}=={.02,.04}


def test_day_forward_no_same_day_training_market_overlap():
    audit,scores=train(tape("2026-10-07")+tape("2026-10-08"))
    folds=[r for r in audit["folds"] if r["day_utc"]=="2026-10-08"]
    assert all(r["status"]=="scored" for r in folds)
    for r in folds:
        assert all(mid.startswith("2026-10-07") for mid in r["fitted"]["training_market_ids"])
    assert audit["formal_oos_result"] is None and audit["confirmed_strategies"]==0
    assert len(scores)==24*4+24


def test_late_received_outcomes_block_fit_even_when_market_ended():
    audit,_=train(tape("2026-10-07",outcome_delay=3*86400000)+tape("2026-10-08"))
    for fold in audit["folds"]:
        if fold["model_id"]!="market_raw":
            assert fold["status"]=="blocked" and fold["train_markets"]==0


def test_prediction_day_label_mutation_does_not_change_any_scores_or_fit():
    rows=tape("2026-10-07")+tape("2026-10-08")
    audit,scores=train(rows)
    altered=copy.deepcopy(rows)
    for r in altered:
        if r["type"]=="outcome" and r["market_id"].startswith("2026-10-08"):
            r["winner"]="Down" if r["winner"]=="Up" else "Up"
    other,score2=train(altered)
    assert scores==score2
    assert [r["fitted"] for r in audit["folds"]]==[r["fitted"] for r in other["folds"]]


def test_missing_future_features_fail_closed_without_imputation():
    rows=tape("2026-10-07")+tape("2026-10-08")
    d=next(r for r in rows if r["type"]=="decision" and r["market_id"].startswith("2026-10-08"))
    d["feature_available_ms"]["r_5s"]=d["recv_ms"]+1
    audit,scores=train(rows)
    assert (d["market_id"],d["recv_ms"],"external_path") not in scores
    assert (d["market_id"],d["recv_ms"],"market_raw") in scores


def test_market_weight_invariant_to_replicating_all_rows_of_one_market():
    cutoff=ms("2026-10-08")
    rows=[]
    for i in range(24):
        f=features(.35 if i%2 else .65)
        t=ms("2026-10-07")+i*300000+60000
        rows.append({"market_id":str(i),"recv_ms":t,"features":f,
                     "feature_available_ms":dict.fromkeys(f,t),"target_up":i%2,
                     "settlement_known_ms":t+240000,"market_end_ms":t+240000})
    a=fit_residual(rows,"full_residual",cutoff)
    b=fit_residual(rows+[copy.deepcopy(rows[0]) for _ in range(99)],"full_residual",cutoff)
    np.testing.assert_allclose(a.beta,b.beta,rtol=1e-6,atol=1e-7)
    with pytest.raises(ValueError,match="training_market"):
        a.predict(dict(rows[0],recv_ms=cutoff+1))


def test_cost_margin_uses_ask_and_fee_not_midpoint():
    c=make_candidates()[0]
    f={"prob_market_raw":.55,"ask_up":.54,"ask_down":.48,"decision_fee_rate":.07}
    assert signal(c,f) is None
    f["ask_up"]=.50
    assert signal(c,f)=="Up"
    f.update(prob_market_raw=.45,ask_up=.53,ask_down=.50)
    assert signal(c,f)=="Down"


def test_attachment_uses_current_book_even_when_invalid_for_executor_rejection():
    d={"type":"decision","recv_ms":100,"market_id":"m","features":{},"feature_available_ms":{}}
    book={"type":"book","recv_ms":99,"market_id":"m","up_asks":[[.51,5]],
          "down_asks":[[.50,5]],"fee_rate":.07,"healthy":False}
    score={("m",100,"market_raw"):{"p_up":.60,"model_fit_ms":0,"feature_available_ms":98}}
    rows=list(attach_scores([book,d],score))
    assert rows[-1]["features"]["ask_up"]==.51
    assert signal(make_candidates()[0],rows[-1]["features"])=="Up"
    assert "ask_up" not in d["features"]  # no source mutation


def test_no_lookahead_book_or_score_join():
    d={"type":"decision","recv_ms":100,"market_id":"m","features":{},"feature_available_ms":{}}
    book={"type":"book","recv_ms":101,"market_id":"m","up_asks":[[.51,5]],
          "down_asks":[[.50,5]],"fee_rate":.07}
    assert list(attach_scores([d,book],{}))[0]["features"]["ask_up"] is None
    bad={("m",100,"market_raw"):{"p_up":.60,"model_fit_ms":101,"feature_available_ms":98}}
    with pytest.raises(ValueError,match="future_model"):
        list(attach_scores([d],bad))


def test_undeclared_dates_and_wrong_split_rejected():
    with pytest.raises(ValueError,match="unconsumed"):
        train(tape("2026-10-09"))
    rows=tape("2026-10-07")
    rows[0]["split"]="prospective"
    with pytest.raises(ValueError,match="development"):
        train(rows)


def test_conflicting_duplicate_rows_and_outcomes_rejected():
    rows=tape("2026-10-07")
    duplicate=copy.deepcopy(next(r for r in rows if r["type"]=="decision"))
    duplicate["features"]["mid_up"]+=.01
    with pytest.raises(ValueError,match="conflicting_duplicate"):
        train(rows+[duplicate])
    outcome=copy.deepcopy(next(r for r in rows if r["type"]=="outcome"))
    outcome["winner"]="Up" if outcome["winner"]=="Down" else "Down"
    with pytest.raises(ValueError,match="conflicting_labels"):
        train(rows+[outcome])


def test_invalid_features_and_no_classifier_class_support():
    for model in MODEL_IDS:
        with pytest.raises(ValueError):
            feature_vector(features(1.),model)
    f=features(); f["rv_15s"]=-1
    with pytest.raises(ValueError,match="negative_realized"):
        feature_vector(f,"external_path")
    rows=tape("2026-10-07")+tape("2026-10-08")
    for row in rows:
        if row["type"]=="outcome" and row["market_id"].startswith("2026-10-07"):
            row["winner"]="Up"
    audit,_=train(rows)
    assert all(r.get("reason")=="insufficient_training_class_markets" for r in audit["folds"]
               if r["day_utc"]=="2026-10-08" and r["model_id"]!="market_raw")
