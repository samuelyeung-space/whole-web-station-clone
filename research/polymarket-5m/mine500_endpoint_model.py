"""Matched-support ablation: does external endpoint information add value?

Two fixed eight-leaf residual models, both trained and scored on IDENTICAL
causally eligible rows, with/without endpoint returns. Same prior-day labels,
market weights and fixed hyperparameters as the existing tree research. Two
entry margins per model are four adaptive DEVELOPMENT trials. No new OOS claim.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

import mine500_deep_batch as batch
import mine500_endpoint as endpoint
import mine500_probability_models as prior
import mine500_replay_v2 as execution
import mine500_tree_models as trees
from mine500_reference_supplement import validate_summary_coverage

VERSION='endpoint-matched-support-ablation-v1'
MODELS=('matched_market', 'matched_endpoint')
REQUIRED=('mid_up','tau_s','ep_r_5s','ep_r_15s','ep_r_30s','ep_r_60s')


def vector(row, model_id):
    if model_id not in MODELS: raise ValueError('unknown model')
    for k in REQUIRED:
        if not prior._finite(row['features'].get(k)) or not prior._finite(row['feature_available_ms'].get(k)):
            raise ValueError('missing endpoint support')
        if row['feature_available_ms'][k] > row['recv_ms']:
            raise ValueError('future endpoint feature')
    base=prior.feature_vector(row['features'],'market_calibration').tolist()
    if model_id=='matched_endpoint':
        r=[prior._clip(row['features'][f'ep_r_{s}s']/5) for s in (5,15,30,60)]
        base += r+[r[0]-r[1]/3,r[1]-r[3]/4]
    return base


def eligible_training(row, cutoff, prediction_ids):
    if str(row['market_id']) in prediction_ids: return False
    if row['target_up'] is None: return False
    clocks=('recv_ms','market_end_ms','settlement_known_ms')
    if not all(prior._finite(row.get(k)) for k in clocks): return False
    return (prior._day(row['recv_ms']) in batch.CONSUMED_DAYS
        and prior._day(row['recv_ms']) < prior._day(cutoff)
        and row['recv_ms'] < row['market_end_ms'] <= row['settlement_known_ms'] < cutoff)


def train(events):
    rows, input_audit=prior._collect(events,batch.CONSUMED_DAYS)
    folds,scores=[],{}
    for day in batch.CONSUMED_DAYS:
        cutoff=prior._start(day)
        test=[r for r in rows if prior._day(r['recv_ms'])==day]
        test_ids={str(r['market_id']) for r in test}
        training=[r for r in rows if eligible_training(r,cutoff,test_ids)]
        valid_train=[];valid_test=[];invalid=Counter()
        for label,source,dest in [('train',training,valid_train),('score',test,valid_test)]:
            for row in source:
                try: vector(row,'matched_endpoint')
                except ValueError as e: invalid[label+':'+str(e)]+=1;continue
                dest.append(row)
        labels={str(r['market_id']):r['target_up'] for r in valid_train}
        classes=Counter(labels.values());groups=Counter(str(r['market_id']) for r in valid_train)
        training_hash=prior._hash(valid_train)
        common=dict(day=day,cutoff_ms=cutoff,train_rows=len(valid_train),train_markets=len(groups),
            classes=dict(classes),train_hash=training_hash,invalid_rows=dict(invalid),
            training_market_ids=sorted(groups),test_rows=len(valid_test),
            latest_training_label_ms=max((r['settlement_known_ms'] for r in valid_train),default=None))
        for model_id in MODELS:
            fold=dict(common,model_id=model_id,metrics=None,learned_state=None)
            if len(groups)<40 or min(classes.get(0,0),classes.get(1,0))<10:
                folds.append(dict(fold,status='blocked_insufficient_training_markets'));continue
            model=HistGradientBoostingRegressor(**trees.FIXED_PARAMETERS,max_leaf_nodes=8)
            weights=[1/groups[str(r['market_id'])] for r in valid_train]
            with threadpool_limits(limits=1):
                model.fit([vector(r,model_id) for r in valid_train],
                          [r['target_up']-r['features']['mid_up'] for r in valid_train],sample_weight=weights)
                residual=model.predict([vector(r,model_id) for r in valid_test]) if valid_test else []
            predictions=np.clip(np.array([r['features']['mid_up'] for r in valid_test])+residual,.001,.999)
            learned=trees._learned_state(model); model_hash=prior._hash([model_id,training_hash,learned])
            for row,p in zip(valid_test,predictions):
                if row['recv_ms']<cutoff or str(row['market_id']) in labels: raise ValueError('train/predict contamination')
                scores[(str(row['market_id']),row['recv_ms'],model_id)]={
                    'p_up':float(p),'model_fit_ms':cutoff,'model_hash':model_hash,
                    'feature_available_ms':max(row['feature_available_ms'][k] for k in REQUIRED)}
            folds.append(dict(fold,status='scored',metrics=prior._prediction_metrics(valid_test,predictions),
                model_hash=model_hash,learned_state=learned,training_weight_per_market=1,
                scored_markets=len({r['market_id'] for r in valid_test})))
    return dict(version=VERSION,folds=folds,input_audit=input_audit,formal_oos_result=None),scores


def candidates():
    result=[]
    for model in MODELS:
        for margin in (.02,.04):
            c=dict(id=f'EPM-{model}-{round(margin*100):02d}',model_id=model,margin=margin,
                required_features=['ep_prob_'+model,'ask_up','ask_down','decision_fee_rate'],
                family='market_prior_conditional_residual',independent_mechanism_claim=False,
                novelty='matched_support_external_information_ablation',selection_status='adaptive_development')
            c['rule_fingerprint']=execution.fingerprint(c);result.append(c)
    return result


def attach(events,scores):
    for row in prior.attach_scores(events,{}):
        if row['type']=='decision':
            row=dict(row,features=dict(row['features']),feature_available_ms=dict(row['feature_available_ms']))
            for model in MODELS:
                value=scores.get((str(row['market_id']),row['recv_ms'],model))
                if value and max(value['model_fit_ms'],value['feature_available_ms'])>row['recv_ms']:
                    raise ValueError('future model score')
                key='ep_prob_'+model
                row['features'][key]=value['p_up'] if value else None
                row['feature_available_ms'][key]=max(value['model_fit_ms'],value['feature_available_ms']) if value else None
        yield row


def signal(c,f):
    return prior.signal(c,dict(f,**{'prob_'+c['model_id']:f['ep_prob_'+c['model_id']]}))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True)
    args=ap.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    reg=candidates()
    batch.write_json(out/'registration.json',dict(version=VERSION,candidates=reg,input_sha256=batch.digest(args.input),
        registered_at_utc=datetime.now(timezone.utc).isoformat(),code_sha256=batch.digest(__file__),
        known_trial_lower_bound=901,parameters=dict(trees.FIXED_PARAMETERS,max_leaf_nodes=8),
        consumed_dates=batch.CONSUMED_DAYS,formal_oos_result=None))
    # Validate input metadata and every receipt clock/date through the existing
    # development combiner. This materializes a bounded-memory stream.
    events,audit=batch.combine_inputs([args.input],materialize_output=out/'checked_input.jsonl.gz')
    # combine_inputs preserves the original disjoint interval manifest only for
    # an individual source; use the original verified combined header for replay.
    original=next(iter(execution.read_jsonl(args.input)))
    if not original.get('observed_intervals_ms'):raise ValueError('missing observed interval manifest')
    events=batch.ReplayTape(Path(args.input),len(events))
    fitted,scores=train(events);batch.write_json(out/'model_audit.json',fitted)
    batch.write_gzip(out/'scores.jsonl.gz',(dict(market_id=k[0],recv_ms=k[1],model_id=k[2],**v) for k,v in scores.items()))
    target=out/'enriched.jsonl.gz';count=batch.write_gzip(target,attach(events,scores));tape=batch.ReplayTape(target,count)
    metrics,daily=[],[]
    for name,changes in batch.SCENARIOS:
        if name not in ('size5','size20','extra_1c'):continue
        cfg=execution.ReplayConfig(require_constraints=True,**changes)
        result=execution.replay_events(tape,reg,signal,cfg)
        validate_summary_coverage(reg,result['summary'],cfg.latencies_ms)
        daily += [dict(r,scenario=name) for r in batch.describe(result)]
        metrics += [dict(r,scenario=name) for r in result['summary']]
        batch.write_gzip(out/f'{name}_attempts.jsonl.gz',result['attempts'])
        batch.write_json(out/f'{name}_audit.json',{k:v for k,v in result.items() if k!='attempts'})
        print(name,'completed',flush=True)
    batch.write_json(out/'metrics.json',metrics);batch.write_json(out/'daily.json',daily)
    batch.write_json(out/'completion.json',dict(candidates=4,scenarios=len(metrics),formal_oos_result=None,confirmed_strategies=0))


if __name__=='__main__':main()
