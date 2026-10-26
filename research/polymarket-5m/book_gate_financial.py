"""Consumed-development WAIT100/control comparison on off-grid projections.

Reuses the existing execution/fee/cash engine. Allocation legs and the formal
seven-day leaderboard are outside this bounded integration.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import mine500_replay_v2 as engine


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()


def catalog():
    return [dict(id='BG108-'+name,required_features=['experiment_kind','experiment_side'],
                 parameters=dict(experiment_kind=k),development_only=True)
            for k,name in enumerate(['IMMEDIATE100','BALANCE_WAIT100','VACUUM_WAIT100'])]


def signal(candidate,features):
    if candidate['parameters']['experiment_kind']!=features['experiment_kind']:return None
    return 'Up' if features['experiment_side']==1 else 'Down'


def merged_events(normalized,projection,cohort,gate_audit,protocol_sha):
    snapshots=projection['requests']
    if len({r['request_id'] for r in snapshots})!=len(snapshots):
        raise ValueError('duplicate projections')
    by_id={r['request_id']:r for r in snapshots}
    arms={r['market_id']:r for r in cohort}
    if len(arms)!=len(cohort) or any(r['side'] not in ('Up','Down') for r in cohort):
        raise ValueError('invalid original cohort')
    gate_by_market={('balance' if r['status']=='balance_ready' else 'vacuum',r['parentkey']):r
                    for r in gate_audit['gate_events']}
    events=[]; decisions={}; metadata={}
    # Only receipt-stamped metadata/constraints/official outcomes are inherited.
    # Old one-second books and decisions never enter this off-grid tape.
    for row in engine.read_jsonl(normalized):
        if row['type'] in ('dataset','market','constraint','outcome'):
            row=dict(row)
            if row['type']=='dataset':
                row.update(split='development',book_resolution='arbitrary_receipt_projection',
                           input_manifest_sha256=protocol_sha,formal_oos_eligible=False,
                           candidate_manifest_sha256=engine.fingerprint(catalog()))
            events.append((row['recv_ms'],0,row))
    for snap in snapshots:
        kind,mid,horizon=snap['request_id'].split(':');horizon=int(horizon)
        if kind not in ('parent','balance','vacuum') or mid not in arms:
            raise ValueError('projection outside fixed cohort')
        decision=by_id[f'{kind}:{mid}:0']
        if horizon not in (0,500,600,1000) or snap['at_ms']!=decision['at_ms']+horizon:
            raise ValueError('invalid matching projection horizon')
        parent=arms[mid]
        if kind=='parent':
            if decision['at_ms']!=parent['decision_ms']:raise ValueError('changed original arm')
        else:
            gate=gate_by_market.get((kind,mid))
            if not gate or decision['at_ms']!=gate['recv_ms'] or decision['at_ms']<parent['decision_ms']:
                raise ValueError('changed or missing causal gate')
        # Always preserve an invalid snapshot as invalid. A reconnect cannot
        # turn an in-flight order into a healthy same-session match.
        healthy=bool(snap['observed_horizon_covered'] and
                     snap['source_epoch']==decision['source_epoch'] and
                     all(t['valid'] for t in snap['tokens'].values()))
        event=dict(type='projected_book',market_id=mid,recv_ms=snap['at_ms'],
                   projection=snap,healthy=healthy)
        events.append((snap['at_ms'],1,event))
        if horizon==0:
            if (kind,mid) in decisions:raise ValueError('duplicate decision')
            decisions[kind,mid]=True
            event=dict(type='projected_decision',market_id=mid,recv_ms=snap['at_ms'],
                       experiment_kind={'parent':0,'balance':1,'vacuum':2}[kind],
                       experiment_side=1 if parent['side']=='Up' else -1)
            events.append((snap['at_ms'],2,event))
    if {mid for kind,mid in decisions if kind=='parent'}!=set(arms):
        raise ValueError('missing original cohort projection')
    for _,_,row in sorted(events,key=lambda e:(e[0],e[1])):
        mid=row.get('market_id');now=row['recv_ms']
        if row['type']=='market':metadata[mid]=row
        if row['type']=='projected_book':
            m=metadata.get(mid)
            if m is None:raise ValueError('no received market metadata')
            snap=row['projection'];up=snap['tokens'].get(m['up_token_id']);down=snap['tokens'].get(m['down_token_id'])
            if up is None or down is None:raise ValueError('both direct tokens required')
            for t in (up,down):
                if t['recv_ms'] is not None and t['recv_ms']>now:raise ValueError('future quote')
            row=dict(type='book',market_id=mid,recv_ms=now,healthy=row['healthy'],
                     up_asks=up['asks'],down_asks=down['asks'],up_bids=up['bids'],down_bids=down['bids'],
                     up_recv_ms=up['recv_ms'],down_recv_ms=down['recv_ms'],fee_rate=m.get('fee_rate'),
                     direct_source_epoch=snap['source_epoch'])
        elif row['type']=='projected_decision':
            m=metadata[mid];features=dict(tau_s=(m['end_ms']-now)/1000,
                experiment_kind=row['experiment_kind'],experiment_side=row['experiment_side'])
            row=dict(type='decision',market_id=mid,recv_ms=now,features=features,
                     feature_available_ms={k:now for k in features})
        yield row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();protocol=json.load(open(args.protocol))
    for value in protocol['inputs'].values():
        if sha(value['path'])!=value['sha256']:raise ValueError('input hash mismatch')
    for name,digest in protocol['code_sha256'].items():
        if sha(Path(__file__).parent/name)!=digest:raise ValueError('execution hash mismatch: '+name)
    inputs=protocol['inputs'];out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    projection=json.load(open(inputs['projection']['path']))
    horizons={}
    for r in projection['requests']:
        kind,mid,h=r['request_id'].split(':');horizons.setdefault((kind,mid),set()).add(int(h))
    if any(h!={0,500,600,1000} for h in horizons.values()):
        raise ValueError('incomplete projection scenarios')
    cohort=json.load(open(inputs['intake_protocol']['path']))['cohort']
    gates=json.load(open(inputs['gate_audit']['path']))
    configs=[engine.ReplayConfig((500,600,1000),100,1000,require_constraints=True),
             engine.ReplayConfig((500,),100,1000,require_constraints=True,depth_fraction=.5),
             engine.ReplayConfig((500,),100,1000,require_constraints=True,extra_cost_per_filled_share_usd=.01)]
    metrics=[]
    for index,config in enumerate(configs):
        result=engine.replay_events(merged_events(inputs['normalized']['path'],projection,cohort,gates,sha(args.protocol)),catalog(),signal,config)
        result.update(protocol_sha256=sha(args.protocol),full_week_pnl=None,formal_oos_result=None,
            fixed_parent_cohort_size=len(cohort),missed_parent_signals_retained=len(cohort)-len(gates['gate_events']),
            evidence_class='consumed_single_day_development',allocation_legs_executed=False)
        with gzip.open(out/f'scenario{index}.json.gz','wt') as f:json.dump(result,f,ensure_ascii=False)
        for row in result['summary']:metrics.append(dict(row,scenario_group=index,full_week_pnl=None,formal_oos_result=None))
    (out/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(summary_rows=len(metrics),financial_scenarios_executed=15,
                          full_week_pnl=None,formal_oos_result=None)))


if __name__=='__main__':main()
