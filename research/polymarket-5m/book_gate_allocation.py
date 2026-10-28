"""Book-state allocations on a frozen parent cohort; development simulation only.

Routes explicit parent/gate events into the existing shared-cash/depth leg
engine. A price discount is not a trigger. Each leg commits its current ask at
its own decision and makes one FAK attempt, without retrying partial fills.
"""
import argparse,gzip,json
from pathlib import Path
import book_gate_financial as source
import patient_entry_replay as execution

POLICIES={'NOW100':((0,100),),'NOW200':((0,200),),'WAIT100':((1,100),),
          'BASE25_GATE75':((0,25),(1,75)),
          'BASE50_GATE50':((0,50),(1,50)),
          'BASE100_GATE100':((0,100),(1,100))}

def legs():
    return [dict(id=f'BG108::{policy}::L{i}',required_features=['tau_s','patient_side','patient_limit'],
        _ledger_id=f'BG108::{policy}',_parent_id='BG108',_policy=policy,_ordinal=i,
        _patient_quantity=quantity,_phase=phase)
        for policy,plan in POLICIES.items() for i,(phase,quantity) in enumerate(plan)]

def route(events,cohort):
    books={};parents={r['market_id']:r for r in cohort}
    for event in events:
        mid=event.get('market_id');kind=event['type']
        if kind=='book':books[mid]=event
        if kind!='decision':
            yield event;continue
        f=event['features'];now=event['recv_ms'];side='up' if f['experiment_side']==1 else 'down'
        asks=books.get(mid,{}).get(side+'_asks',[])
        limit=asks[0][0] if asks else 0.
        for leg in legs():
            if leg['_phase']!=f['experiment_kind']:continue
            features=dict(tau_s=f['tau_s'],patient_side=f['experiment_side'],patient_limit=limit)
            yield dict(type='decision',market_id=mid,recv_ms=now,patient_dispatch_id=leg['id'],
                patient_parent_first_ms=parents[mid]['decision_ms'],features=features,
                feature_available_ms=dict(tau_s=now,patient_side=parents[mid]['decision_ms'],patient_limit=now))

def main():
    p=argparse.ArgumentParser();p.add_argument('--normalized',required=True);p.add_argument('--checkpoint',required=True)
    p.add_argument('--output',required=True);p.add_argument('--register-only',action='store_true');a=p.parse_args()
    ck=Path(a.checkpoint);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    inputs={'normalized':Path(a.normalized),'projection':ck/'offgrid_projection_audit.json',
            'intake_protocol':ck/'book_gate_intake_protocol.json','gate_audit':ck/'book_gate_intake_audit.json'}
    old=json.load(open(ck/'book_gate_financial_protocol.json'))
    for k,v in inputs.items():
        if source.sha(v)!=old['inputs'][k]['sha256']:raise ValueError('restored input differs: '+k)
    here=Path(__file__).parent
    protocol=dict(schema='book-gate-allocation-v1',development_only=True,policies=POLICIES,
        inputs={k:dict(path=str(v.resolve()),sha256=source.sha(v)) for k,v in inputs.items()},
        code_sha256={n:source.sha(here/n) for n in ['book_gate_allocation.py','book_gate_financial.py','patient_entry_replay.py','mine500_replay_v2.py','mine500_constraints.py']},
        cash_per_policy_scenario=1000,reset_or_borrow=False,cohort=30,gate_markets=7,
        scenarios=['500ms','600ms','1000ms','500ms_half_depth','500ms_extra1cent'],
        leg_limits='ask observed at that leg decision; frozen through match',
        parent_health_not_used_to_reselect=True,partial_fills_retried=False,
        evidence='consumed single UTC day4.502669h; not formal seven-day ranking or OOS',
        volume_model='displayed depth upper bound; shared within policy; real receipts uncalibrated',
        min_order_and_tick='original received constraints required; incomplete tick-change stream',
        qualification=None,formal_oos_result=None,full_week_pnl=None)
    path=out/'protocol.json';encoded=json.dumps(protocol,ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text()!=encoded:raise ValueError('registered protocol changed')
    path.write_text(encoded)
    if a.register_only:return
    projection=json.load(open(inputs['projection']));cohort=json.load(open(inputs['intake_protocol']))['cohort'];gates=json.load(open(inputs['gate_audit']))
    configs=[source.engine.ReplayConfig((500,600,1000),100,1000,require_constraints=True),
        source.engine.ReplayConfig((500,),100,1000,require_constraints=True,depth_fraction=.5),
        source.engine.ReplayConfig((500,),100,1000,require_constraints=True,extra_cost_per_filled_share_usd=.01)]
    metrics=[]
    for i,config in enumerate(configs):
        tape=source.merged_events(inputs['normalized'],projection,cohort,gates,source.sha(path))
        result=execution._leg_replay_events(route(tape,cohort),legs(),execution._leg_signal,config)
        result.update(protocol_sha256=source.sha(path),evidence_class=protocol['evidence'],
            fixed_parent_cohort=30,gate_markets=7,untriggered_gate_markets=23,
            fractional_legs_are_auxiliary_not100share_order_ranking=True,formal_oos_result=None,full_week_pnl=None)
        with gzip.open(out/f'scenario{i}.json.gz','wt') as f:json.dump(result,f,ensure_ascii=False)
        metrics.extend(dict(r,scenario_group=i,formal_oos_result=None,full_week_pnl=None) for r in result['summary'])
    (out/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(rows=len(metrics),full_week_pnl=None,formal_oos_result=None)))

if __name__=='__main__':main()
