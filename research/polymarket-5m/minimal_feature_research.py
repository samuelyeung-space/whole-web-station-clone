"""New, separately registered causal minimal-feature model ablation.

Never extends or modifies an existing frozen model. Fits only prior consumed
development dates with official outcomes locally known before each UTC cutoff.
Two minimal feature sets crossed with fixed-old versus expanded training rows.
"""
import argparse,gzip,hashlib,heapq,json,math
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from eco_input_audit import number,sha
from score_prerequisite_audit import Trace
from minimal_feature_strategy import candidates

MODEL_PARAMETERS={'max_iter':80,'max_leaf_nodes':7,'l2_regularization':10,
    'learning_rate':.05,'min_samples_leaf':100,'random_state':17,'early_stopping':False}
RETURNS=('ep_r_5s','ep_r_15s','ep_r_60s','ep_r_30s')
DAY=86400000


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def read(path):
    header=False
    with gzip.open(path,'rt') as stream:
        for line in stream:
            event=json.loads(line)
            if event['type']=='dataset':
                if header or event.get('split')!='development':raise ValueError('one development header required')
                header=True
            elif not header:raise ValueError('missing development header')
            yield event
    if not header:raise ValueError('missing development header')


def events(paths):
    yield from heapq.merge(*((e for e in read(p) if e['type']!='dataset') for p in paths),key=lambda e:e['recv_ms'])


def vector(event,book):
    t=event['recv_ms'];f=event['features'];av=event['feature_available_ms']
    if not all(number(f.get(k)) and number(av.get(k)) and av[k]<=t for k in ('mid_up','tau_s')):return None
    if not 0<f['mid_up']<1 or not 0<f['tau_s']<=300:return None
    if not book or book.get('recv_ms',t+1)>t:return None
    if book.get('up_healthy',book.get('healthy')) is not True:return None
    receipt=book.get('up_recv_ms')
    if not number(receipt) or not 0<=t-receipt<=250:return None
    asks=book.get('up_asks',[]);bids=book.get('up_bids',[])
    if not asks or not bids or not 0<bids[0][0]<asks[0][0]<1:return None
    if abs(f['mid_up']-(asks[0][0]+bids[0][0])/2)>1e-10:return None
    p=max(.001,min(.999,f['mid_up']));z=math.log(p/(1-p));tau=f['tau_s']
    base=[p,z,tau/300,(p-.5)/math.sqrt(max(tau,1)),z*math.log1p(tau),asks[0][0]-bids[0][0]]
    result={'time':base}
    if all(number(f.get(k)) and number(av.get(k)) and av[k]<=t for k in RETURNS):
        result['trend']=base+[f[k] for k in RETURNS]+[f['ep_r_5s']-f['ep_r_15s']/3,f['ep_r_15s']-f['ep_r_60s']/4]
    return {'market':event['market_id'],'t':t,'day':int(t//DAY),'p':p,'vectors':result,
            'saved_reference':{m:f.get(m+'_5') for m in ('time','trend')}}


def eligible_training(rows,labels,markets,day,model,training,old_keys):
    cutoff=day*DAY
    return [r for r in rows if r['day']<day and model in r['vectors']
        and (training=='expanded' or (r['market'],r['t']) in old_keys)
        and r['market'] in labels and labels[r['market']][1]<cutoff
        and markets[r['market']]['end_ms']<cutoff]


def execution_inputs(event,book):
    t=event['recv_ms']
    if not book or book.get('healthy') is not True or book.get('recv_ms',t+1)>t:return {}
    if not number(book.get('fee_rate')) or book['fee_rate']<0:return {}
    for side in ('up','down'):
        receipt=book.get(side+'_recv_ms');asks=book.get(side+'_asks',[])
        if not number(receipt) or not 0<=t-receipt<=250:return {}
        if not asks or not 0<asks[0][0]<1:return {}
    return {'mfa_ask_up':book['up_asks'][0][0],'mfa_ask_down':book['down_asks'][0][0],'mfa_fee':book['fee_rate']}


def prepare(registration,input_root,output):
    reg=json.loads(Path(registration).read_text())
    if reg.get('split')!='already_consumed_development':raise ValueError('consumed development only')
    paths=[]
    for spec in reg['inputs']:
        p=Path(input_root)/Path(spec['output']).name
        if sha(p)!=spec['output_sha256']:raise ValueError('source tape hash mismatch')
        paths.append(p)
    if len(paths)!=len(set(paths)):raise ValueError('duplicate inputs')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    code={n:sha(Path(__file__).with_name(n)) for n in ('minimal_feature_research.py','minimal_feature_strategy.py','score_prerequisite_audit.py','eco_input_audit.py')}
    registration_out={'schema':'minimal-feature-ablation-v1','registered_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_role':'already_consumed_development','source_registry_sha256':sha(registration),
        'source_tapes':{p.name:sha(p) for p in paths},'candidates':candidates(),'code_sha256':code,
        'model_parameters':MODEL_PARAMETERS,'minimum_prior_training_markets':40,
        'time_features':'same six original JPR base components; causal fresh direct Up book only',
        'trend_features':'time features + four external returns + two acceleration contrasts',
        'training_policies':{'fixed':'same original JPR vector-eligible rows; original prior-date/known-label constraints',
                             'expanded':'all prior-date rows eligible for this minimal feature set; not an old-model extension'},
        'prediction_policy':'day-by-day earlier-date training; no future label, no score carry-forward',
        'execution':'unchanged dual-token engine, fixed limit partial FAK, $1000 target100, 500/600/1000ms and costs/depth',
        'formal_oos_result':None,'new_rule_variants':8,'new_independent_mechanisms':0}
    dump=lambda name,value:(out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    dump('registration.json',registration_out)
    rows=[];labels={};markets={};books={};trace=Trace();seen=set();coverage=Counter()
    for e in events(paths):
        trace.feed(e)
        kind=e['type'];mid=e.get('market_id')
        if kind=='market':markets[mid]=e
        elif kind=='book':books[mid]=e
        elif kind=='outcome':
            if e.get('winner') not in ('Up','Down'):raise ValueError('official binary outcome required')
            label=(int(e['winner']=='Up'),max(e['recv_ms'],e['resolved_ms']))
            if mid in labels and labels[mid]!=label:raise ValueError('conflicting official outcomes')
            labels[mid]=label
        elif kind=='decision':
            coverage['all_decisions']+=1;r=vector(e,books.get(mid))
            if r is not None:
                if (mid,r['t']) in seen:raise ValueError('duplicate model row')
                seen.add((mid,r['t']));rows.append(r)
                for model in r['vectors']:coverage[model+'_rows']+=1
    old_keys=trace.eligible_keys
    if old_keys-seen:raise ValueError(f'minimal vectors unexpectedly reject {len(old_keys-seen)} original rows')
    coverage['old_vector_rows']=len(old_keys);coverage['registered_markets']=len(markets);coverage['official_labels']=len(labels)
    dump('feature_coverage.json',dict(coverage));print(json.dumps({'feature_coverage':dict(coverage)}),flush=True)
    scores={};audit=[];parity=Counter();maxdiff={}
    for day in sorted({r['day'] for r in rows}):
        for model in ('time','trend'):
            test=[r for r in rows if r['day']==day and model in r['vectors']]
            if not test:continue
            for training in ('fixed','expanded'):
                key=f'mfa_{model}_{training}'
                train=eligible_training(rows,labels,markets,day,model,training,old_keys)
                counts=Counter(r['market'] for r in train);ids=set(counts)
                rec={'day':day,'model':model,'training':training,'score_key':key,
                     'train_rows':len(train),'train_markets':len(ids),'test_rows':len(test),'test_markets':len({r['market'] for r in test})}
                if len(ids)<40:rec['status']='warmup_no_fit';audit.append(rec);continue
                if ids.intersection(r['market'] for r in test):raise ValueError('training/test market overlap')
                X=np.array([r['vectors'][model] for r in train]);xt=np.array([r['vectors'][model] for r in test])
                y=np.array([labels[r['market']][0]-r['p'] for r in train]);weights=np.array([1/counts[r['market']] for r in train]);weights=weights/weights.mean()
                estimator=HistGradientBoostingRegressor(**MODEL_PARAMETERS)
                with threadpool_limits(limits=1):estimator.fit(X,y,sample_weight=weights);pred=estimator.predict(xt)
                rec.update(status='fit',latest_training_label_ms=max(labels[k][1] for k in ids),
                    training_identity=fingerprint({'x':X.tolist(),'y':y.tolist(),'markets':sorted(ids)}))
                for r,value in zip(test,pred):
                    q=float(np.clip(r['p']+value,.001,.999));scores.setdefault((r['market'],r['t']),{})[key]=q
                    saved=r['saved_reference'][model]
                    if training=='fixed' and (r['market'],r['t']) in old_keys and number(saved):
                        diff=abs(q-saved);parity[key]+=1;maxdiff[key]=max(maxdiff.get(key,0),diff)
                audit.append(rec);print(json.dumps(rec),flush=True)
    if any(x>1e-10 for x in maxdiff.values()):raise ValueError('fixed training prediction parity with old stored scores failed')
    dump('model_audit.json',audit);dump('fixed_train_prediction_parity.json',{'compared_rows':dict(parity),'max_abs_error':maxdiff,'tolerance':1e-10})
    with gzip.open(out/'scores.jsonl.gz','wt',compresslevel=1) as stream:
        for (mid,t),value in sorted(scores.items(),key=lambda x:(x[0][1],x[0][0])):
            stream.write(json.dumps({'market_id':mid,'recv_ms':t,'scores':value},separators=(',',':'))+'\n')
    enriched=[]
    for p in paths:
        dest=out/p.name;counts=Counter();current_books={}
        with gzip.open(dest,'wt',compresslevel=1) as stream:
            for e in read(p):
                if e['type']=='dataset':
                    e['dataset_id']+=':MFA-v1';e['mfa_registration_sha256']=sha(out/'registration.json')
                elif e['type']=='book':current_books[e['market_id']]=e
                elif e['type']=='decision':
                    counts['decisions']+=1
                    extra={**scores.get((e['market_id'],e['recv_ms']),{}),**execution_inputs(e,current_books.get(e['market_id']))}
                    for key,value in extra.items():
                        if key in e['features']:raise ValueError('feature collision')
                        e['features'][key]=value;e['feature_available_ms'][key]=e['recv_ms'];counts[key]+=1
                stream.write(json.dumps(e,separators=(',',':'),allow_nan=False)+'\n')
        enriched.append({'input':str(p),'input_sha256':sha(p),'output':str(dest),'output_sha256':sha(dest),'counts':dict(counts)})
        print(json.dumps({'enriched':p.name,'counts':dict(counts)}),flush=True)
    dump('completion.json',{'source_role':'consumed_development','fitting_completed':True,'input_hashes_verified':True,
        'feature_coverage':dict(coverage),'inputs':enriched,'scores_sha256':sha(out/'scores.jsonl.gz'),
        'new_candidates':8,'economic_replays_completed':False,'formal_oos_result':None})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('registration','input-root','output'):p.add_argument('--'+name,required=True)
    a=p.parse_args();prepare(a.registration,a.input_root,a.output)
