"""Censored entry-leg audit; never completes a special-case exit/hedge test."""
import argparse,gzip,hashlib,json
from pathlib import Path
import mine500_replay_v2 as execution


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()


def tape(rows,intent,protocol_hash):
    at=intent['at_ms'];mid=intent['market_id'];sent=False;seen=set()
    for raw in rows:
        now=raw['recv_ms'];kind=raw['type']
        if now>at+1000:break
        if kind=='dataset':
            if raw.get('book_resolution')!='decision_and_match_projection' or at%raw['decision_grid_ms']:
                raise ValueError('entry is not covered by existing fixed projection grid')
            if not {500,600,1000}.issubset(raw['supported_latencies_ms']):raise ValueError('missing matching horizons')
            yield dict(raw,split='development',coverage_end_ms=at+1000,
                       input_manifest_sha256=protocol_hash,formal_oos_eligible=False)
        elif raw.get('market_id')==mid and kind in ('market','constraint','book'):
            yield raw
            if kind=='book' and now in (at,at+500,at+600,at+1000):seen.add(now)
            if kind=='book' and now==at and not sent:
                sent=True
                yield dict(type='decision',market_id=mid,recv_ms=at,
                    features={'tau_s':(intent['market_end_ms']-at)/1000,'entry_side':1 if intent['side']=='Up' else -1},
                    feature_available_ms={'tau_s':at,'entry_side':at})
    if not sent or seen!={at,at+500,at+600,at+1000}:
        raise ValueError('missing exact entry/matching projection; no stale substitution')


def signal(candidate,features):return 'Up' if features['entry_side']==1 else 'Down'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--protocol',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();p=json.load(open(a.protocol));out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    for v in p['inputs'].values():
        if sha(v['path'])!=v['sha256']:raise ValueError('input hash mismatch')
    for n,d in p['code_sha256'].items():
        if sha(Path(__file__).parent/n)!=d:raise ValueError('code hash mismatch')
    intent=p['intent'];configs=[execution.ReplayConfig((500,600,1000),100,1000,require_constraints=True),
        execution.ReplayConfig((500,),100,1000,require_constraints=True,depth_fraction=.5),
        execution.ReplayConfig((500,),100,1000,require_constraints=True,extra_cost_per_filled_share_usd=.01)]
    metrics=[]
    for i,cfg in enumerate(configs):
        result=execution.replay_events(tape(execution.read_jsonl(p['inputs']['normalized']['path']),intent,sha(a.protocol)),
            [dict(id='SPECIAL-ENTRY',required_features=['entry_side'])],signal,cfg)
        result.update(evidence_class='consumed_development_entry_leg_only',exit_and_hedge_evaluated=False,
            full_strategy_pnl=None,formal_oos_result=None,full_week_pnl=None,protocol_sha256=sha(a.protocol))
        with gzip.open(out/f'scenario{i}.json.gz','wt') as f:json.dump(result,f)
        for row in result['summary']:metrics.append(dict(row,scenario_group=i,full_strategy_pnl=None))
    (out/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
    print(json.dumps(metrics))


if __name__=='__main__':main()
