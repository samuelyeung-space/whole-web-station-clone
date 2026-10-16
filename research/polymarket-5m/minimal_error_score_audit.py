"""Independent score-clock continuity audit, no labels/signals/PnL."""
import argparse,gzip,json
from collections import Counter
from pathlib import Path
from replay_lab import sha

def audit(scores,completion,out):
    last={};runs={};hist=Counter();deltas=Counter();counts=Counter();jumps=Counter();previous={}
    with gzip.open(scores,'rt') as stream:
        for line in stream:
            row=json.loads(line)
            for model in ('fixed','expanded'):
                name='mfa_trend_'+model
                if name not in row['scores']:continue
                key=(row['market_id'],model);t=row['recv_ms'];q=row['scores'][name]
                delta=t-last[key] if key in last else None
                if delta is not None:
                    if delta<=0:raise ValueError('duplicate/nonmonotone score clock')
                    deltas[(model,delta)]+=1
                if delta==1000:
                    runs[key]+=1
                    diff=abs(q-previous[key]);jumps[(model,'consecutive_pairs')]+=1
                    for limit in (.01,.05,.1):jumps[(model,str(limit))]+=int(diff>=limit)
                else:
                    if key in runs:hist[(model,runs[key])]+=1
                    runs[key]=1
                counts[model]+=1;last[key]=t;previous[key]=q
    for key,n in runs.items():hist[(key[1],n)]+=1
    completed=json.loads(Path(completion).read_text());result=[]
    for model in ('fixed','expanded'):
        h={n:c for (m,n),c in hist.items() if m==model}
        ready={f'h{horizon}':sum((n-4*horizon+1)*c for n,c in h.items() if n>=4*horizon) for horizon in (5,15)}
        for hname,value in ready.items():
            if value!=completed['counts'][model+'_'+hname]:raise ValueError('independent readiness mismatch')
        result.append({'model':model,'score_rows':counts[model],'runs':sum(h.values()),
            'runs_lt20':sum(c for n,c in h.items() if n<20),'max_run':max(h),
            'independent_readiness':ready,'run_histogram':h,
            'gap_ms_histogram':{d:c for (m,d),c in deltas.items() if m==model},
            'absolute_score_jump_threshold_counts':{k:c for (m,k),c in jumps.items() if m==model}})
    data={'status':'passed','scores_sha256':sha(scores),'feature_completion_sha256':sha(completion),
        'models':result,'no_signal_or_outcome_evaluation':True,
        'interpretation':'score absence fragments model history; no interpolation or relaxed warmup; jumps are descriptive not proof of model error',
        'formal_oos_result':None}
    Path(out).write_text(json.dumps(data,indent=2)+'\n');print(json.dumps({'status':'passed','models':[{k:r[k] for k in ('model','score_rows','runs','runs_lt20','independent_readiness')} for r in result]}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--scores',required=True);p.add_argument('--completion',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();audit(a.scores,a.completion,a.out)
