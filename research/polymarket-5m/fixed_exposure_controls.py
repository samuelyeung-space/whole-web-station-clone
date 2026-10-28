"""Auxiliary NOW25/NOW50 controls; exact frozen parent queues, not rank7."""
import argparse,gzip,json,hashlib
from pathlib import Path
import parent_book_allocations as base

def legs():
    return [dict(id=f'EXPOSURE::NOW{q}::L0',required_features=['tau_s','patient_side','patient_limit'],
        _ledger_id=f'EXPOSURE::NOW{q}',_parent_id='EXPOSURE',_policy=f'NOW{q}',_ordinal=0,
        _patient_quantity=q,_phase=0) for q in (25,50)]

def route(events,cohort):
    books={};parents={r['market_id']:r for r in cohort}
    for e in events:
        mid=e.get('market_id')
        if e['type']=='book':books[mid]=e
        if e['type']!='decision':yield e;continue
        f=e['features'];now=e['recv_ms']
        if f['experiment_kind']!=0:raise ValueError('control received a gate decision')
        asks=books.get(mid,{}).get(('up' if f['experiment_side']==1 else 'down')+'_asks',[])
        limit=asks[0][0] if asks else 0.
        for leg in legs():
            yield dict(type='decision',market_id=mid,recv_ms=now,patient_dispatch_id=leg['id'],
                patient_parent_first_ms=parents[mid]['decision_ms'],
                features=dict(tau_s=f['tau_s'],patient_side=f['experiment_side'],patient_limit=limit),
                feature_available_ms=dict(tau_s=now,patient_side=parents[mid]['decision_ms'],patient_limit=now))

def atomic_json_gzip(path,value):
    blob=gzip.compress(json.dumps(value,ensure_ascii=False).encode());json.loads(gzip.decompress(blob))
    path=Path(path);tmp=path.with_suffix('.pending');tmp.write_bytes(blob);tmp.replace(path)
    if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(blob).digest():
        raise IOError('output byte verification failed')

def run(root):
    root=Path(root);out=root/'exposure_controls';out.mkdir(exist_ok=True)
    cohort_path=root/'checkpoint/run232/cohorts/cohorts.json.gz'
    projection_path=root/'checkpoint/run237/joined_projections.json.gz'
    c=base.load(cohort_path);pp=base.load(projection_path);projections={}
    for r in pp['requests']:
        kind,aid,h=r['request_id'].split(':')
        if kind=='parent':projections.setdefault((kind,aid),{})[int(h)]=r
    here=Path(__file__).parent
    protocol=dict(schema='fixed-exposure-controls-v1',development_only=True,
        code={n:base.source.sha(here/n) for n in ['fixed_exposure_controls.py','parent_book_allocations.py','book_gate_financial.py','patient_entry_replay.py','mine500_replay_v2.py','mine500_constraints.py']},
        inputs={str(p.relative_to(root)):base.source.sha(p) for p in [cohort_path,projection_path,root/'metadata.jsonl']},
        quantities=[25,50],independent_cash=1000,reset_or_borrow=False,
        execution='same causal parent quote projection, fixed-at-decision ask limit, one FAK; no gate selection',
        scenarios=['500ms','600ms','1000ms','500ms_half_depth','500ms_extra1cent'],
        class_label='consumed single-day auxiliary exposure attribution; not100share rank7',
        full_week_pnl=None,formal_oos_result=None)
    path=out/'protocol.json';encoded=json.dumps(protocol,ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text()!=encoded:raise ValueError('protocol changed')
    path.write_text(encoded);ph=base.source.sha(path)
    records=[r for r in c['candidate_records'] if r['cohort']];cache={};metrics=[];done=[]
    for rec in records:
        cid=rec['candidate_id'];key=tuple(rec['cohort'])
        if key in cache:
            original,rows=cache[key];rows=[dict(r,parent_rule=cid,execution_alias_of=original) for r in rows]
        else:
            cohort,p,g=base.inputs_for(rec,c,projections,{},'balance');assert not g['gate_events']
            configs=[base.source.engine.ReplayConfig((500,600,1000),100,1000,require_constraints=True),
                base.source.engine.ReplayConfig((500,),100,1000,require_constraints=True,depth_fraction=.5),
                base.source.engine.ReplayConfig((500,),100,1000,require_constraints=True,extra_cost_per_filled_share_usd=.01)]
            results=[];rows=[]
            for sg,config in enumerate(configs):
                tape=base.source.merged_events(root/'metadata.jsonl',p,cohort,g,ph)
                result=base.execution._leg_replay_events(route(tape,cohort),legs(),base.execution._leg_signal,config)
                result.update(parent_rule=cid,scenario_group=sg,full_week_pnl=None,formal_oos_result=None)
                results.append(result)
                rows.extend(dict(r,parent_rule=cid,scenario_group=sg,full_week_pnl=None,formal_oos_result=None) for r in result['summary'])
            atomic_json_gzip(out/(cid+'.json.gz'),results);cache[key]=cid,rows
        metrics.extend(rows);done.append(cid)
        (out/'progress.json').write_text(json.dumps(dict(completed=done,unique_executions=len(cache),total=len(records))))
    (out/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(parents=len(done),unique_executions=len(cache),metric_rows=len(metrics))))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();run(a.root)
