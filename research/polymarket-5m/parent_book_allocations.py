"""Generic frozen-parent dispatch; existing allocation/FAK engine unchanged.

Input arm IDs are rekeyed only inside each isolated complete parent queue.
Balance and vacuum gates never share a ledger or overwrite one another.
"""
import argparse,gzip,json,hashlib
from pathlib import Path
import book_gate_financial as source
import book_gate_allocation as allocation
import patient_entry_replay as execution

def load(path):
    with (gzip.open(path,'rt') if str(path).endswith('.gz') else open(path)) as f:return json.load(f)

def inputs_for(record,cohorts,projections,gates,family):
    ids=record['cohort'];arms=cohorts['unique_arms']
    cohort=[dict(arms[k],arm_id=k) for k in ids]
    if len({r['market_id'] for r in cohort})!=len(cohort):raise ValueError('duplicate parent market')
    projection=[];selected=[]
    for aid in ids:
        market=arms[aid]['market_id']
        for phase in ('parent',family):
            key=(phase,aid)
            if key not in projections:
                if phase=='parent':raise ValueError('missing original parent input')
                continue
            if set(projections[key])!={0,500,600,1000}:raise ValueError('missing scenario')
            for h,r in projections[key].items():projection.append(dict(r,request_id=f'{phase}:{market}:{h}'))
        if (family,aid) in gates:
            r=dict(gates[family,aid]);r['parentkey']=market;selected.append(r)
    return cohort,{'requests':projection},{'gate_events':selected}

def route(tape,cohort,family):
    # Both gate families use phase1 of the same allocation plan, in SEPARATE runs.
    for r in allocation.route((dict(e,features=dict(e['features'],experiment_kind=1))
            if e['type']=='decision' and e['features']['experiment_kind']==2 else e for e in tape),cohort):
        yield r

def run(record,cohorts,projections,gates,metadata,protocol_hash):
    results=[];metrics=[]
    for family in ('balance','vacuum'):
        cohort,p,g=inputs_for(record,cohorts,projections,gates,family)
        configs=[source.engine.ReplayConfig((500,600,1000),100,1000,require_constraints=True),
                 source.engine.ReplayConfig((500,),100,1000,require_constraints=True,depth_fraction=.5),
                 source.engine.ReplayConfig((500,),100,1000,require_constraints=True,extra_cost_per_filled_share_usd=.01)]
        for scenario,config in enumerate(configs):
            tape=source.merged_events(metadata,p,cohort,g,protocol_hash)
            result=execution._leg_replay_events(route(tape,cohort,family),allocation.legs(),execution._leg_signal,config)
            result.update(parent_rule=record['candidate_id'],gate_family=family,scenario_group=scenario,
                          parent_cohort_size=len(cohort),gate_count=len(g['gate_events']),
                          full_week_pnl=None,formal_oos_result=None)
            results.append(result)
            metrics.extend(dict(r,parent_rule=record['candidate_id'],gate_family=family,scenario_group=scenario,
                                parent_cohort_size=len(cohort),gate_count=len(g['gate_events']),full_week_pnl=None,formal_oos_result=None) for r in result['summary'])
    return results,metrics

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--only108',action='store_true');a=p.parse_args()
    root=Path(a.root);out=root/'financial';out.mkdir(exist_ok=True)
    c=load(root/'checkpoint/run232/cohorts/cohorts.json.gz')
    pp=load(root/'checkpoint/run237/joined_projections.json.gz');gg=load(root/'checkpoint/run237/joined_gate_events.json.gz')
    projections={};gates={}
    for r in pp['requests']:
        kind,aid,h=r['request_id'].split(':');projections.setdefault((kind,aid),{})[int(h)]=r
    for r in gg['gate_events']:
        family='balance' if r['status']=='balance_ready' else 'vacuum';gates[family,r['arm_id']]=r
    records=[r for r in c['candidate_records'] if r['cohort']]
    records.sort(key=lambda r:(r['candidate_id']!='M500-108',r['candidate_id']))
    protocol=dict(schema='parent-book-allocations-v1',consumed_development=True,
        inputs={str(f):source.sha(root/f) for f in ['metadata.jsonl','normalized.gz','checkpoint/run232/cohorts/cohorts.json.gz','checkpoint/run237/joined_projections.json.gz','checkpoint/run237/joined_gate_events.json.gz']},
        code={n:source.sha(Path(__file__).parent/n) for n in ['parent_book_allocations.py','book_gate_allocation.py','book_gate_financial.py','patient_entry_replay.py','mine500_replay_v2.py','mine500_constraints.py']},
        candidate_ids=[r['candidate_id'] for r in records],policies=allocation.POLICIES,
        cash_per_parent_family_policy_scenario=1000,partial_fills_retried=False,
        first_parent_signals_unchanged=True,families_separate=True,full_week_pnl=None,formal_oos_result=None)
    path=out/'protocol.json';encoded=json.dumps(protocol,indent=2)+'\n'
    if path.exists() and path.read_text()!=encoded:raise ValueError('protocol differs')
    path.write_text(encoded);ph=source.sha(path)
    all_metrics=[];cache={};done=[]
    for rec in records:
        if a.only108 and rec['candidate_id']!='M500-108':continue
        key=tuple(rec['cohort']);cid=rec['candidate_id']
        if key in cache:
            prior,rows=cache[key];rows=[dict(r,parent_rule=cid,execution_alias_of=prior) for r in rows]
        else:
            results,rows=run(rec,c,projections,gates,root/'metadata.jsonl',ph)
            with gzip.open(out/(cid+'.json.gz'),'wt') as f:json.dump(results,f)
            cache[key]=(cid,rows)
        all_metrics.extend(rows);done.append(cid)
        (out/'progress.json').write_text(json.dumps(dict(completed=done,total=len(records),unique_executions=len(cache))))
        if cid=='M500-108':
            expected={'NOW100':123.20053,'NOW200':155.83820,'WAIT100':143.57477,'BASE25_GATE75':163.65169,'BASE50_GATE50':171.80815,'BASE100_GATE100':266.77530}
            for r in rows:
                if r['gate_family']=='balance' and r['scenario_group']==0 and r['latency_ms']==500:
                    policy=r['candidate_id'].split('::')[1]
                    if abs(r['net_pnl_usd']-expected[policy])>1e-6:raise AssertionError('108 parity: '+policy)
            print('108 parity passed',flush=True)
    (out/'metrics.json').write_text(json.dumps(all_metrics,indent=2)+'\n')
    print(json.dumps(dict(completed=len(done),unique_executions=len(cache),metric_rows=len(all_metrics))),flush=True)

if __name__=='__main__':main()
