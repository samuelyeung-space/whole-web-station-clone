"""Synthetic input-integrity checks, not evidence of strategy returns."""
import copy
import json
from dataclasses import asdict

import pytest

import mine500_joint52 as joint


def fixtures():
    ids1={f'PATH-{i:02}' for i in range(40)}
    ids2={f'MPT-{i:02}' for i in range(12)}
    config=asdict(joint.common.execution.ReplayConfig())
    config['latencies_ms']=list(config['latencies_ms'])
    docs=[{'config':dict(config,initial_cash_usd=1e12),'attempts':[],
           'summary':[dict(candidate_id=cid,latency_ms=lag) for cid in sorted(ids) for lag in joint.LATENCIES]}
          for ids in (ids1,ids2)]
    return docs,ids1,ids2,config


def test_all_52_candidates_retained_without_pnl_selection():
    docs,a,b,config=fixtures()
    result=joint.combine_intents(*docs,a,b,config)
    assert len(result['summary'])==52*3
    assert result['attempts']==[]


@pytest.mark.parametrize('change',['cash','latency','shares','cost'])
def test_config_and_uncensored_cash_must_match(change):
    docs,a,b,config=fixtures()
    key,value={'cash':('initial_cash_usd',1000),'latency':('latencies_ms',[250]),
               'shares':('shares',10),'cost':('extra_cost_per_filled_share_usd',.01)}[change]
    docs[1]['config'][key]=value
    with pytest.raises(ValueError,match='configs differ'):
        joint.combine_intents(*docs,a,b,config)


def test_cash_censored_attempt_is_rejected():
    docs,a,b,config=fixtures()
    docs[0]['attempts']=[dict(candidate_id=min(a),market_id='m',latency_ms=500,
                             status='rejected_insufficient_available_cash')]
    with pytest.raises(ValueError,match='capital censored'):
        joint.combine_intents(*docs,a,b,config)


def test_incomplete_first_attempt_beats_later_profit_and_remains_null():
    docs,a,b,config=fixtures()
    earlier=dict(candidate_id=min(a),market_id='m',latency_ms=500,decision_ms=1,
                 side='Up',status='incomplete_match_horizon',filled_shares=0,settled=False)
    later=dict(candidate_id=min(b),market_id='m',latency_ms=500,decision_ms=2,
               side='Down',status='filled_full',filled_shares=5,settled=True,net_pnl_usd=100)
    docs[0]['attempts']=[earlier]
    docs[1]['attempts']=[later]
    combined=joint.combine_intents(*docs,a,b,config)
    result=joint.common.panel_results(combined)[0]
    assert result['status']=='blocked_incomplete_panel_execution'
    assert result['net_pnl_usd'] is None
    assert len(result['chosen_attempts'])==1
    assert result['chosen_attempts'][0]['candidate_id']==min(a)


def test_duplicate_or_missing_summaries_rejected():
    docs,a,b,config=fixtures()
    docs[0]['summary'].append(copy.deepcopy(docs[0]['summary'][0]))
    with pytest.raises(ValueError,match='summaries'):
        joint.combine_intents(*docs,a,b,config)


def test_input_hash_and_shared_code_drift_rejected(monkeypatch):
    docs,a,b,config=fixtures()
    hashes={name:'a'*64 for name in ('mine500_deep_batch.py','mine500_replay_v2.py','mine500_shared_portfolio.py')}
    regs=[dict(candidates=[{'id':cid} for cid in ids],input_hashes={'data':'b'*64},
               code_hashes=copy.deepcopy(hashes),registered_at_utc='2026-10-11T01:00:00+00:00') for ids in (a,b)]
    protocol=dict(schema='joint52-outcome-blind-panel-protocol-v1',candidate_ids=list(a|b),
        candidate_count=52,new_panel_selection_trials=1,known_multiplicity_lower_bound_including_zoo100=873,
        scenarios=list(joint.SCENARIOS),latencies_ms=list(joint.LATENCIES),results=None,
        shared_portfolio_cash_usd=1000,intent_cash_ceiling_usd=1e12,registered_at_utc='2026-10-11T00:00:00+00:00')
    monkeypatch.setattr(joint.common,'digest',lambda _: 'a'*64)
    joint.validate_registrations(*regs,protocol)
    regs[1]['input_hashes']={'data':'c'*64}
    with pytest.raises(ValueError,match='input hashes'):
        joint.validate_registrations(*regs,protocol)
    regs[1]['input_hashes']=regs[0]['input_hashes']
    regs[1]['code_hashes']['mine500_replay_v2.py']='c'*64
    with pytest.raises(ValueError,match='dependency hashes'):
        joint.validate_registrations(*regs,protocol)


def test_batch_must_be_completed_and_not_invalidated(tmp_path):
    with pytest.raises(FileNotFoundError):
        joint.batch_metadata(tmp_path,40)
    (tmp_path/'invalidated_run.json').write_text('{}')
    with pytest.raises(ValueError,match='invalidated'):
        joint.batch_metadata(tmp_path,40)
