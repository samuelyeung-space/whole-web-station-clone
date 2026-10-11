"""Fixed nonlinear market-prior residual trials on consumed development only.

Six histogram tree fits and two entry margins are twelve parameter trials, not
twelve independent mechanisms. Each UTC-day fit uses only prior-day markets
whose official label was received before midnight. Never random-split rows.
The entire input remains reused development; this module cannot certify OOS.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import hashlib
import math

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

import mine500_probability_models as prior

VERSION = 'histogram_market_prior_residual_v1'
CONSUMED_DAYS = ('2026-10-07', '2026-10-08', '2026-10-09')
FEATURE_GROUPS = ('market_calibration', 'external_path', 'full_residual')
LEAF_COUNTS = (4, 8)
MARGINS = (.02, .04)
MODEL_SPECS = {f'{group}_L{leaves}': (group, leaves)
               for group in FEATURE_GROUPS for leaves in LEAF_COUNTS}
MODEL_IDS = tuple(MODEL_SPECS)
MIN_TRAIN_MARKETS = 40
MIN_CLASS_MARKETS = 10
FIXED_PARAMETERS = dict(loss='squared_error', max_iter=50, learning_rate=.05,
    max_depth=None, l2_regularization=10., min_samples_leaf=100,
    early_stopping=False, validation_fraction=None, random_state=1729,
    max_bins=255, max_features=1.0)


def _learned_state(model):
    """Auditable learned numbers; no pickle execution needed to inspect a fit."""
    return {'baseline':model._baseline_prediction.tolist(),
        'bin_thresholds':[r.tolist() for r in model._bin_mapper.bin_thresholds_],
        'trees':[[{'node_fields':list(tree.nodes.dtype.names),
                    'nodes':tree.nodes.tolist(),
                    'raw_left_cat_bitsets':tree.raw_left_cat_bitsets.tolist(),
                    'binned_left_cat_bitsets':tree.binned_left_cat_bitsets.tolist()}
                  for tree in stage] for stage in model._predictors]}


@dataclass
class FittedTree:
    model_id: str
    estimator: HistGradientBoostingRegressor
    fit_cutoff_ms: int
    training_market_ids: tuple[str, ...]
    training_hash: str
    training_weight_sums: dict[str, float]

    def checked_vector(self, row):
        if not prior._finite(row.get('recv_ms')) or row['recv_ms'] < self.fit_cutoff_ms:
            raise ValueError('prediction_precedes_fit_cutoff')
        if str(row['market_id']) in self.training_market_ids:
            raise ValueError('training_market_cannot_be_scored_forward')
        return prior._causal_vector(row, MODEL_SPECS[self.model_id][0])

    def predict_many(self, rows):
        if not rows:
            return []
        x=np.asarray([self.checked_vector(row) for row in rows])
        with threadpool_limits(limits=1):
            residual=self.estimator.predict(x)
        mid=np.asarray([row['features']['mid_up'] for row in rows])
        if not np.isfinite(residual).all():
            raise ValueError('nonfinite_tree_prediction')
        return np.clip(mid+residual,.001,.999).tolist()

    def predict(self, row):
        return self.predict_many([row])[0]

    def as_dict(self):
        learned=_learned_state(self.estimator)
        parameters=dict(FIXED_PARAMETERS,max_leaf_nodes=MODEL_SPECS[self.model_id][1])
        descriptor={'model_id':self.model_id,'feature_group':MODEL_SPECS[self.model_id][0],
            'fit_cutoff_ms':self.fit_cutoff_ms,'training_market_ids':list(self.training_market_ids),
            'training_hash':self.training_hash,'parameters':parameters,
            'sklearn_version':sklearn.__version__,'n_iter':self.estimator.n_iter_,
            'training_weight_sums_by_market':self.training_weight_sums,
            'learned_state':learned,'learned_state_hash':prior._hash(learned),
            'target':'official_Up_indicator_minus_received_Up_midpoint',
            'prediction':'clip(received_Up_midpoint + fitted_residual, .001, .999)'}
        descriptor['model_hash']=prior._hash(descriptor)
        return descriptor


def fit_residual(rows, model_id, cutoff_ms):
    """Fit precollected eligible rows, with fail-closed direct-call guards."""
    if model_id not in MODEL_SPECS:
        raise ValueError('unknown_model')
    if (not prior._finite(cutoff_ms) or cutoff_ms != prior._start(prior._day(cutoff_ms))
            or prior._day(cutoff_ms) not in CONSUMED_DAYS):
        raise ValueError('fit_cutoff_must_be_consumed_UTC_midnight')
    group,leaves=MODEL_SPECS[model_id]
    groups,labels=Counter(),{}
    xs,ys=[],[]
    for row in rows:
        mid=str(row['market_id'])
        if any(not prior._finite(row.get(k)) for k in
               ('recv_ms','settlement_known_ms','market_end_ms')):
            raise ValueError('invalid_training_clock')
        if (prior._day(row['recv_ms']) not in CONSUMED_DAYS
                or prior._day(row['recv_ms']) >= prior._day(cutoff_ms)
                or row['settlement_known_ms'] >= cutoff_ms or row['market_end_ms'] >= cutoff_ms
                or row['settlement_known_ms'] < row['market_end_ms']
                or row['recv_ms'] >= row['market_end_ms']):
            raise ValueError('training_label_or_market_not_available_before_cutoff')
        target=row['target_up']
        if target not in (0,1) or isinstance(target,bool):
            raise ValueError('invalid_label')
        if mid in labels and labels[mid] != target:
            raise ValueError('conflicting_labels')
        labels[mid]=target
        groups[mid]+=1
        xs.append(prior._causal_vector(row,group))
        ys.append(target-row['features']['mid_up'])
    if len(groups)<MIN_TRAIN_MARKETS:
        raise ValueError('insufficient_training_markets')
    counts=Counter(labels.values())
    if min(counts.get(0,0),counts.get(1,0))<MIN_CLASS_MARKETS:
        raise ValueError('insufficient_training_class_markets')
    weights=np.asarray([1./groups[str(row['market_id'])] for row in rows])
    model=HistGradientBoostingRegressor(**FIXED_PARAMETERS,max_leaf_nodes=leaves)
    with threadpool_limits(limits=1):
        model.fit(np.asarray(xs),np.asarray(ys),sample_weight=weights)
    if model.n_iter_ != FIXED_PARAMETERS['max_iter']:
        raise ValueError('unexpected_early_stopping')
    training_hash=prior._hash([{key:row[key] for key in
        ('market_id','recv_ms','features','feature_available_ms','target_up',
         'settlement_known_ms','market_end_ms')} for row in rows])
    weight_sums={mid:math.fsum([1./count]*count) for mid,count in sorted(groups.items())}
    return FittedTree(model_id,model,cutoff_ms,tuple(sorted(groups)),training_hash,weight_sums)


def train_forward_days(events, *, consumed_days, input_hashes):
    if (not input_hashes or any(not isinstance(v,str) or len(v)!=64
            or any(c not in '0123456789abcdef' for c in v) for v in input_hashes.values())):
        raise ValueError('explicit_sha256_input_hashes_required')
    days=sorted(set(consumed_days))
    if not days or not set(days).issubset(CONSUMED_DAYS):
        raise ValueError('only_declared_consumed_development_days_allowed')
    rows,input_audit=prior._collect(events,days)
    folds,scores=[],{}
    for day in days:
        cutoff=prior._start(day)
        test=[row for row in rows if prior._day(row['recv_ms'])==day]
        test_ids={str(row['market_id']) for row in test}
        for model_id,(group,leaves) in MODEL_SPECS.items():
            training,eligible,invalid=[],[],Counter()
            for row in rows:
                if prior._day(row['recv_ms'])>=day or str(row['market_id']) in test_ids:
                    continue
                if (row['target_up'] is None or row['settlement_known_ms']>=cutoff
                        or row['market_end_ms']>=cutoff):
                    invalid['training_outcome_not_known_before_day']+=1
                    continue
                try:
                    prior._causal_vector(row,group)
                except ValueError as exc:
                    invalid['training:'+str(exc)]+=1
                    continue
                training.append(row)
            labels={str(row['market_id']):row['target_up'] for row in training}
            fold={'day_utc':day,'model_id':model_id,'feature_group':group,'max_leaf_nodes':leaves,
                'evidence_class':'reused_development_forward_diagnostic',
                'train_days':sorted({prior._day(row['recv_ms']) for row in training}),
                'train_markets':len(labels),'train_rows':len(training),
                'training_class_market_counts':dict(Counter(labels.values())),
                'latest_training_label_known_ms':max((r['settlement_known_ms'] for r in training),default=None),
                'prediction_rows_total':len(test),'prediction_markets_total':len(test_ids),
                'fit_cutoff_ms':cutoff,'invalid_rows':dict(invalid),'metrics':None,'fitted':None}
            try:
                fitted=fit_residual(training,model_id,cutoff)
            except ValueError as exc:
                fold.update(status='blocked',reason=str(exc),scored_rows=0,scored_markets=0)
                folds.append(fold)
                continue
            for row in test:
                try:
                    fitted.checked_vector(row)
                except ValueError as exc:
                    invalid['prediction:'+str(exc)]+=1
                    continue
                eligible.append(row)
            predicted=fitted.predict_many(eligible)
            descriptor=fitted.as_dict()
            for row,p in zip(eligible,predicted):
                scores[(str(row['market_id']),row['recv_ms'],model_id)]={
                    'p_up':p,'model_fit_ms':cutoff,'model_hash':descriptor['model_hash'],
                    'feature_available_ms':max(row['feature_available_ms'][k] for k in prior.REQUIRED[group])}
            fold.update(status='scored',fitted=descriptor,invalid_rows=dict(invalid),
                scored_rows=len(predicted),scored_markets=len({r['market_id'] for r in eligible}),
                metrics=prior._prediction_metrics(eligible,predicted))
            folds.append(fold)
    return {'version':VERSION,'evidence_class':'reused_development_forward_diagnostic',
        'registered_trials':12,'model_specs':dict(MODEL_SPECS),'fixed_parameters':FIXED_PARAMETERS,
        'minimum_training_markets':MIN_TRAIN_MARKETS,'minimum_markets_per_class':MIN_CLASS_MARKETS,
        'fixed_cost_margins':MARGINS,'input_sha256':dict(input_hashes),
        'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'consumed_days_utc':days,'input_audit':input_audit,'folds':folds,
        'formal_oos_result':None,'confirmed_strategies':0,'independent_economic_mechanisms_claimed':0,
        'limitations':['No random validation or same-day label fitting.',
            'All 12 trials and prior adaptive screening remain in the multiplicity denominator.',
            'Repeated decision rows have total training weight one per market; raw min_samples_leaf is not an independent market count.',
            'Three dates and displayed depth do not establish significance or actual execution.']},scores


def make_candidates():
    return [{'id':f'MPT-{model_id}-{round(margin*100):02d}','model_id':model_id,
        'feature_group':MODEL_SPECS[model_id][0],'max_leaf_nodes':MODEL_SPECS[model_id][1],
        'family':'nonlinear_market_prior_conditional_residual','margin':margin,
        'required_features':['tree_prob_'+model_id,'ask_up','ask_down','decision_fee_rate'],
        'novelty':'nonlinear_comparison_with_existing_residual_not_independent_mechanism',
        'status':'registered_development_only'} for model_id in MODEL_IDS for margin in MARGINS]


def attach_scores(events,score_map):
    # Reuse prior book attachment with no linear scores, then exact-key tree join.
    for row in prior.attach_scores(events,{}):
        if row.get('type')!='decision':
            yield row
            continue
        features,available=dict(row['features']),dict(row['feature_available_ms'])
        for model_id in MODEL_IDS:
            value=score_map.get((str(row['market_id']),row['recv_ms'],model_id))
            key='tree_prob_'+model_id
            if value:
                if (not all(prior._finite(value.get(k)) for k in
                        ('model_fit_ms','feature_available_ms','p_up'))
                        or not .001<=value['p_up']<=.999):
                    raise ValueError('invalid_tree_score')
                if max(value['model_fit_ms'],value['feature_available_ms'])>row['recv_ms']:
                    raise ValueError('future_model_or_features_in_score_map')
            features[key]=value['p_up'] if value else None
            available[key]=max(value['model_fit_ms'],value['feature_available_ms']) if value else None
        yield dict(row,features=features,feature_available_ms=available)


def signal(candidate,features):
    # Same ask + fee entry rule as the original probability module.
    group=candidate['model_id']
    return prior.signal(candidate,dict(features,**{'prob_'+group:features.get('tree_prob_'+group)}))
