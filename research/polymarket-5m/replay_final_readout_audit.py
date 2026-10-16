"""Read-only audit of completed JPR/MAR/portfolio report fields and representative ledgers.

Does not import the replay engine, generate signals, fit models, or execute orders.
The only display override removes no-fill return confidence intervals; original
cash-path accounting, summaries and completed ledgers are kept unchanged.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

KEYS = ('run_key', 'scenario', 'candidate_id', 'latency_ms')
REQUIRED = ('attempts', 'submitted_orders', 'filled_orders', 'full_fills',
    'partial_fills', 'failed_or_unfilled_attempts', 'not_submitted_attempts',
    'submitted_but_unfilled_orders', 'signal_markets', 'filled_markets',
    'registered_markets', 'recorded_hours', 'complete_recording_dates',
    'filled_shares', 'fees_usd', 'extra_cost_usd', 'recorded_scope_net_pnl_usd',
    'recorded_scope_pnl_per_filled_share_usd', 'unresolved_positions',
    'max_realized_drawdown_usd', 'min_available_cash_usd', 'initial_cash_usd',
    'ending_cash_usd', 'day_cluster_uncertainty', 'engine_status',
    'capital_scope', 'drawdown_basis', 'net_pnl_usd', 'formal_oos_result')


def parse(v):
    if v == '': return None
    try: return json.loads(v)
    except (ValueError, TypeError): return v


def key(row): return tuple(row[k] for k in KEYS)


def eq(a, b):
    if isinstance(a, (float,int)) and not isinstance(a,bool) and isinstance(b,(float,int)) and not isinstance(b,bool):
        return math.isclose(a,b,abs_tol=1e-7,rel_tol=1e-10)
    return a == b


def uncertainty_override(row):
    if row['filled_orders']:
        return None
    return {'status':'not_evaluable_no_fills', 'day_clusters':2,
        'mean_observed_day_pnl_95pct_interval_usd':None,
        'settled_share_pnl_95pct_interval_usd':None,
        'qualified_edge':False,
        'reason':'No filled observations; unchanged cash is not an estimated return distribution.'}


def run(root):
    root=Path(root).resolve(); hashes={}; discrepancies=[]
    def digest(path):
        h=hashlib.sha256()
        with Path(path).open('rb') as f:
            for block in iter(lambda:f.read(2**20),b''):h.update(block)
        return h.hexdigest()
    def use(path):
        path=Path(path);hashes[str(path.relative_to(root))]=digest(path);return path
    def read(path):return json.loads(use(path).read_text())
    def csvread(path):
        with use(path).open() as f:return [{k:parse(v) for k,v in r.items()} for r in csv.DictReader(f)]
    def check(ok, what, **detail):
        if not ok:discrepancies.append({'check':what,**detail})
    def checkrow(row, expected, what):
        for field,value in expected.items():
            check(field in row and eq(row.get(field),value),what,key=list(key(row)),field=field,reported=row.get(field),expected=value)

    comparison_files=[f'reports/{family}_{kind}/{kind}_comparison_all_rules.csv'
        for family in ('jpr','mar') for kind in ('capacity','stress')]
    all_rows=[]
    for name in comparison_files:all_rows.extend(csvread(root/name))
    shared=read(root/'reports/shared/shared_assessment.json');all_rows.extend(shared['results'])
    indexed={key(r):r for r in all_rows}
    check(len(indexed)==len(all_rows)==3609,'unique_3609_completed_scenarios')
    missing_fields={f:sum(f not in r for r in all_rows) for f in REQUIRED}
    for field,n in missing_fields.items():check(n==0,'missing_critical_report_field',field=field,rows=n)
    status=Counter(r['engine_status'] for r in all_rows)
    no_signal=[r for r in all_rows if r['engine_status']=='evaluated_no_signal']
    no_fills=[r for r in all_rows if r['engine_status']=='evaluated_no_fills']
    for r in all_rows:
        checkrow(r,{'attempts':r['submitted_orders']+r['not_submitted_attempts'],
          'filled_orders':r['full_fills']+r['partial_fills'],
          'failed_or_unfilled_attempts':r['attempts']-r['filled_orders'],
          'submitted_but_unfilled_orders':r['submitted_orders']-r['filled_orders'],
          'registered_markets':336,'complete_recording_dates':0,'initial_cash_usd':1000,
          'net_pnl_usd':None,'formal_oos_result':None},'report_arithmetic_or_scope')
        if r['filled_shares']:
            checkrow(r,{'recorded_scope_pnl_per_filled_share_usd':r['recorded_scope_net_pnl_usd']/r['filled_shares']},'pnl_per_share')
        else:
            checkrow(r,{'recorded_scope_pnl_per_filled_share_usd':None},'undefined_per_share_is_null')
        u=r['day_cluster_uncertainty']
        check(u['day_clusters']==2 and not u['qualified_edge'],'corrected_model_ci_uses_two_dates',key=list(key(r)))
    for r in no_signal:
        checkrow(r,{'attempts':0,'filled_orders':0,'recorded_scope_net_pnl_usd':None,
            'recorded_scope_pnl_per_filled_share_usd':None,'filled_shares':0,'fees_usd':0,'extra_cost_usd':0,
            'unresolved_positions':0,'ending_cash_usd':1000,'min_available_cash_usd':1000},'no_signal_result_null')
    # Zero fills after actual attempts legitimately leave cash PnL zero; no return per fill exists.
    for r in no_fills:
        checkrow(r,{'filled_orders':0,'recorded_scope_net_pnl_usd':0,'recorded_scope_pnl_per_filled_share_usd':None,'filled_shares':0,'fees_usd':0,'extra_cost_usd':0,
            'unresolved_positions':0,'ending_cash_usd':1000},'attempted_zero_fill_accounting')
        check(r['attempts']>0,'zero_fill_requires_actual_attempts',key=list(key(r)))
    representatives=csvread(root/'reports/current_model_representative_rows.csv')
    check(len(representatives)==120,'representative_rows_120')
    for r in representatives:
        source=indexed[key(r)]
        checkrow(r,{f:source[f] for f in REQUIRED if f in r and f!='capital_scope'},'representative_vs_canonical')
        check(source['capital_scope']=='each_candidate_and_latency_has_independent_cash_do_not_sum' and
              r['capital_scope']=='each candidate/latency/scenario has its own $1000 account; never sum',
              'representative_capital_scope_semantically_equivalent',key=list(key(r)))
        check(r['prediction_dates']==['2026-10-08','2026-10-09'],'representative_prediction_dates',key=list(key(r)))
    targets={key(r):indexed[key(r)] for r in representatives}
    targets.update({key(r):r for r in shared['results']})
    runs={}; summaries={}
    for report in ('jpr','jpr_capacity','jpr_stress','mar','mar_capacity','mar_stress','shared'):
        s=read(root/'reports'/report/'summary.json');summaries[report]=s
        check(not s['reconciliation_differences'],'prior_reconciliation_empty',report=report)
        for p in s['provenance']:
            # Resolve portable run identity from local directory, never trust historical absolute path.
            found=list(root.glob(f'*results/{p["run_key"]}'))
            check(len(found)==1,'run_directory_unique',run_key=p['run_key'])
            if len(found)==1:runs[p['run_key']]=found[0]
    grouped=defaultdict(dict)
    for k,r in targets.items():grouped[k[:2]][k[2:]]=r
    audited_attempts=audited_cash_events=0
    for (run_key,scenario),wanted in grouped.items():
        runpath=runs[run_key];completion=read(runpath/'completion.json')
        check(completion['status']=='completed_development_simulation','source_run_completed',run_key=run_key)
        sums={k:{'attempts':0,'submitted_orders':0,'full_fills':0,'partial_fills':0,
          'filled_shares':0.,'fees_usd':0.,'extra_cost_usd':0.,'recorded_scope_net_pnl_usd':0.,
          'unresolved_positions':0,'signal_set':set(),'filled_set':set(),'attempt_ids':set()} for k in wanted}
        apath=use(runpath/scenario/'attempts.jsonl.gz')
        check(hashes[str(apath.relative_to(root))]==completion['output_sha256'][f'{scenario}/attempts.jsonl.gz'],'attempt_hash_matches_completion',run_key=run_key,scenario=scenario)
        with gzip.open(apath,'rt') as f:
            for line in f:
                a=json.loads(line);k=a['candidate_id'],a['latency_ms']
                if k not in wanted:continue
                audited_attempts+=1;s=sums[k]
                check(a['attempt_id'] not in s['attempt_ids'],'unique_attempt_id',run_key=run_key,scenario=scenario,id=a['attempt_id'])
                s['attempt_ids'].add(a['attempt_id']);s['attempts']+=1;s['submitted_orders']+=bool(a.get('submitted'))
                s['full_fills']+=a['status']=='filled_full';s['partial_fills']+=a['status']=='filled_partial'
                s['signal_set'].add(a['market_id']);s['filled_shares']+=a.get('filled_shares',0)
                s['fees_usd']+=a.get('fees_usd',0);s['extra_cost_usd']+=a.get('extra_cost_usd',0)
                if a.get('filled_shares',0)>0:
                    s['filled_set'].add(a['market_id']);s['unresolved_positions']+=not a.get('settled',False)
                    if a.get('net_pnl_usd') is not None:s['recorded_scope_net_pnl_usd']+=a['net_pnl_usd']
        cpath=use(runpath/scenario/'cash_ledger.csv')
        check(hashes[str(cpath.relative_to(root))]==completion['output_sha256'][f'{scenario}/cash_ledger.csv'],'cash_hash_matches_completion',run_key=run_key,scenario=scenario)
        cash={k:{'min_available_cash_usd':1000.,'ending_cash_usd':1000.,'max_realized_drawdown_usd':0.,'peak':0.,'sequence':-1,'time':-1} for k in wanted}
        with cpath.open() as f:
            for raw in csv.DictReader(f):
                c={k:parse(v) for k,v in raw.items()};k=c['candidate_id'],c['latency_ms']
                if k not in wanted or c.get('report_cash_basis')!='engine_recorded_event':continue
                audited_cash_events+=1;s=cash[k]
                check(c['event_sequence']>s['sequence'] and c['event_ms']>=s['time'],'cash_event_order',run_key=run_key,scenario=scenario,key=list(k))
                s['sequence']=c['event_sequence'];s['time']=c['event_ms']
                s['min_available_cash_usd']=min(s['min_available_cash_usd'],c['cash_usd']-c['reserved_usd'])
                s['peak']=max(s['peak'],c['realized_pnl_usd'])
                s['max_realized_drawdown_usd']=max(s['max_realized_drawdown_usd'],s['peak']-c['realized_pnl_usd'])
                s['ending_cash_usd']=c['cash_usd']
        for k,row in wanted.items():
            s=sums[k];s.pop('attempt_ids');s['signal_markets']=len(s.pop('signal_set'));s['filled_markets']=len(s.pop('filled_set'))
            s['filled_orders']=s['full_fills']+s['partial_fills']
            s['not_submitted_attempts']=s['attempts']-s['submitted_orders']
            s['submitted_but_unfilled_orders']=s['submitted_orders']-s['filled_orders']
            s['failed_or_unfilled_attempts']=s['attempts']-s['filled_orders']
            if not s['attempts']:s['recorded_scope_net_pnl_usd']=None
            if s['unresolved_positions']:s['recorded_scope_net_pnl_usd']=None
            s['recorded_scope_pnl_per_filled_share_usd']=s['recorded_scope_net_pnl_usd']/s['filled_shares'] if s['filled_shares'] and s['recorded_scope_net_pnl_usd'] is not None else None
            checkrow(row,s,'representative_attempt_ledger_reconciliation')
            checkrow(row,{f:cash[k][f] for f in ('min_available_cash_usd','ending_cash_usd','max_realized_drawdown_usd')},'representative_cash_ledger_reconciliation')
    prediction=read(root/'saved_prediction_coverage.json')
    check(prediction['model_prediction_dates']==['2026-10-08','2026-10-09'],'prediction_dates_source')
    features=read(root/'mar_feature_day_coverage.json')
    exceptions=[]
    for r in all_rows:
        override=uncertainty_override(r)
        if override is not None:
            exceptions.append({**dict(zip(KEYS,key(r))),'engine_status':r['engine_status'],
               'recorded_scope_net_pnl_usd':r['recorded_scope_net_pnl_usd'],
               'model_return_uncertainty':override,
               'original_cash_path_uncertainty_unchanged':True})
    exceptionpath=root/'final_readout_uncertainty_overrides.json'
    exceptionpath.write_text(json.dumps({'schema':'no-fill-return-uncertainty-display-overrides-v1',
        'new_backtests':0,'source_rows_or_ledgers_modified':False,'rows':exceptions},ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    use(exceptionpath)
    result={'schema':'completed-model-final-readout-audit-v1','status':'passed_with_readout_supplement' if not discrepancies else 'failed',
      'new_backtests':0,'strategy_rules_changed':False,'economic_outputs_modified':False,
      'checked_completed_scenarios':len(all_rows),'critical_fields_missing_rows':{k:v for k,v in missing_fields.items() if v},
      'field_presence_all_rows':list(REQUIRED),'status_counts':dict(status),
      'representative_table_rows_checked':len(representatives),'shared_portfolio_rows_checked':len(shared['results']),
      'representative_table_scope':'Compare every displayed critical field to canonical complete rows; directly audit the 129 canonical target accounts. Compact CSV omits filled_orders (full+partial) and signal_markets (one first attempt per market).',
      'capital_scope_display_normalization':'Compact prose and canonical enum both mean an independent $1000 account per candidate/latency/scenario; exact expected phrases verified.',
      'direct_representative_ledger_check':{'scenario_accounts':len(targets),'run_scenario_filesets':len(grouped),
          'attempt_rows_read':audited_attempts,'cash_events_read':audited_cash_events,
          'completion_hashes_verified':True,'comparison_absolute_tolerance_usd':1e-7},
      'no_signal_policy':{'rows':len(no_signal),'recorded_scope_net_pnl_usd':None,
          'pnl_per_filled_share_usd':None,'unchanged_cash_does_not_count_as_zero_return_test':True},
      'attempted_but_zero_fill_policy':{'rows':len(no_fills),'cash_accounting_pnl_usd':0,
          'pnl_per_filled_share_usd':None,'return_uncertainty':None},
      'readout_gap_fixed':{'issue':'No-fill rows carried [0,0] mean-day bootstrap intervals despite no filled return observations.',
          'affected_no_signal_rows':len(no_signal),'affected_attempted_zero_fill_rows':len(no_fills),
          'supplement':'final_readout_uncertainty_overrides.json',
          'policy':'Model return intervals null when filled_orders=0; keep raw cash-path diagnostics unchanged.'},
      'authoritative_model_uncertainty_sources':comparison_files+['reports/shared/shared_assessment.json'],
      'raw_generic_summary_warning':'reports/*/summary.json retain three-date program cash-path intervals including Oct7 warmup; use the corrected sources above for model inference. Source summaries deliberately not rewritten.',
      'model_uncertainty':{'prediction_dates':['2026-10-08','2026-10-09'],'excluded_warmup_dates':['2026-10-07'],
         'partial_day_clusters':2,'complete_calendar_days':0,
         'interpretation':'Descriptive, unstable with two partial days; not adjusted for selection or serial dependence and not proof of an edge.'},
      'coverage':{'recorded_hours':27.015909729410806,'registered_markets':336,'decision_book_markets':330,
          'jpr_prediction_markets':266,'mar_complete_feature_markets':264,'complete_calendar_days':0,
          'seven_full_days_complete':False,'market_counts_are_distinct_denominators_not_interchangeable':True},
      'capacity_and_cash_scope':{'target_shares':[5,10,20],
         'capacity':'Displayed-book replay upper bounds; 20 shares is not validated live capacity.',
         'isolated':'Each rule/size/latency/stress owns a separate simulated $1000 account; do not add.',
         'shared':'Only the registered six-rule portfolio shares $1000 per alternative size/latency; do not add its nine alternatives.',
         'minimum_cash':'Minimum cash minus outstanding order reservations over original cash ledger.',
         'drawdown':'Realized equity only; excludes mark-to-market inventory drawdown.',
         'cash_release':'Assumed zero delay after locally received official outcome, not measured redemption execution.',
         'half_depth':'Visible matching depth multiplied by 0.5.',
         'plus_1cent':'Extra $0.01 per actually filled share, included in cash reservation; signal limits and matching prices unchanged.'},
      'formal_oos_result':None,'qualified_strategies':0,'reconciliation_differences':discrepancies,
      'audit_code_sha256':digest(__file__),'input_sha256':hashes}
    (root/'final_readout_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    result=run(p.parse_args().root)
    print(json.dumps({**{k:result[k] for k in ('status','checked_completed_scenarios','direct_representative_ledger_check')},'differences':len(result['reconciliation_differences'])},ensure_ascii=False))
    raise SystemExit(bool(result['reconciliation_differences']))
