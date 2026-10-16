"""Read-only exact old-rule parity and matched-control report for MEM."""
import argparse,gzip,json
from pathlib import Path
from collections import defaultdict
from replay_lab import sha
from minimal_error_motion_strategy import candidates

def contrast(actual,control):
    if actual is None or control is None:return None
    return actual-control

def verify(root,old):
    root=Path(root);old=Path(old)
    a=json.loads((root/'assessment.json').read_text());b=json.loads((old/'assessment.json').read_text())
    def path(assessment):return Path(assessment['modes']['isolated']['run_path'])
    def groups(directory):
        out=defaultdict(list)
        with gzip.open(directory/'attempts.jsonl.gz','rt') as stream:
            for line in stream:
                row=json.loads(line);cid=row.pop('candidate_id')
                # Global output row ordinal changes with catalog size; not economics.
                row.pop('attempt_id');out[(cid,row['latency_ms'])].append(row)
        return out
    comparisons=[]
    for scenario in ('shares100_base_release0','shares100_half_depth_release0','shares100_plus_1cent_release0'):
        x,y=groups(path(a)/scenario),groups(path(b)/scenario)
        for new,previous in [('MEM-EDGE-FIXED','MFA-006'),('MEM-EDGE-EXPANDED','MFA-008')]:
            for latency in (500,600,1000):
                if x[(new,latency)]!=y[(previous,latency)]:raise ValueError('full attempt parity failed '+new+scenario+str(latency))
                xx=next(r for r in a['modes']['isolated']['metrics'] if r['scenario']==scenario and r['candidate_id']==new and r['latency_ms']==latency)
                yy=next(r for r in b['modes']['isolated']['metrics'] if r['scenario']==scenario and r['candidate_id']==previous and r['latency_ms']==latency)
                for k in ('net_pnl_usd','fees_usd','attempts','filled_shares','ending_cash_usd','max_realized_drawdown_usd'):
                    if xx[k]!=yy[k]:raise ValueError('summary parity failed '+k)
                comparisons.append({'scenario':scenario,'new':new,'old':previous,'latency_ms':latency,'attempts':len(x[(new,latency)]),'exact_attempts_and_summary_equal':True})
    base={r['candidate_id']:r for r in a['base500_all']};matched=[]
    primary_groups=groups(path(a)/'shares100_base_release0')
    new_attempts=[row for c in candidates()[:8] for row in primary_groups[(c['id'],500)]]
    for c in candidates()[:8]:
        actual,control=base[c['id']],base[c['id']+'-MATCHED']
        stress=[r for r in a['modes']['isolated']['metrics'] if r['candidate_id']==c['id']]
        matched.append({'id':c['id'],'rule':c,'primary':actual,'matched_admission_control':control,
            'pnl_difference_not_causal':contrast(actual['net_pnl_usd'],control['net_pnl_usd']),
            'all9_scenarios_positive':all(r['net_pnl_usd'] is not None and r['net_pnl_usd']>0 for r in stress)})
    result={'status':'passed','assessment_sha256':sha(root/'assessment.json'),'old_assessment_sha256':sha(old/'assessment.json'),
        'exact_original_rule_comparisons':comparisons,'new_rules_with_matched_controls':matched,
        'excluded_identity_fields':['candidate_id','attempt_id (global catalog-dependent row ordinal)'],
        'new_primary_rule_attempts':len(new_attempts),
        'new_primary_unique_signal_markets':len({r['market_id'] for r in new_attempts}),
        'new_primary_unique_decision_utc_dates':len({int(r['decision_ms']//86400000) for r in new_attempts}),
        'warning':'different first signals imply different cash/fill sets; PnL differences are not treatment effects or OOS',
        'formal_oos_result':None}
    (root/'independent_controls.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':'passed','exact_control_comparisons':len(comparisons),'all9_positive_rules':[r['id'] for r in matched if r['all9_scenarios_positive']]}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--old',required=True);a=p.parse_args();verify(a.root,a.old)
