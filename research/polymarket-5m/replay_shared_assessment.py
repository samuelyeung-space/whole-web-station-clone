"""Audit completed shared first-signal arbitration and actual cash mutations."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from historical_batch_summary import day_bootstrap, digest, read_csv


def check(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b):
    return abs(a-b) <= 1e-7


def assess(run, report, portfolio_registration, prediction_coverage, parity_report):
    run, report = Path(run), Path(report)
    read=lambda p:json.loads(Path(p).read_text())
    completion, registration = read(run/'completion.json'), read(run/'registration.json')
    check(completion['status']=='completed_development_simulation','incomplete shared run')
    check(registration['mode']=='shared','not a shared portfolio')
    summary=read(report/'summary.json')
    check(not summary['reconciliation_differences'],'generic cash/attempt reconciliation differs')
    check(summary['provenance'][0]['completion_sha256']==digest(run/'completion.json'),'summary completion lineage differs')
    frozen=read(portfolio_registration)
    check(frozen['new_portfolio_configurations']==1 and frozen['new_candidate_rules']==0,'unexpected portfolio registration')
    priority=frozen['fixed_priority'];rank={cid:i for i,cid in enumerate(priority)}
    check(registration['priority']==priority,'priority differs from registration')
    parity=read(parity_report)
    check(parity['status']=='passed' and len(parity['tapes'])==6,'input projection preflight not passed')
    check(frozen['source_plugin_sha256']['frozen_jpr_strategy']==parity['plugin_source_sha256'],'JPR preflight plugin hash differs')
    prediction_dates=set(read(prediction_coverage)['model_prediction_dates'])
    daily=list(read_csv(report/'daily.csv'))
    results=[];arbitration=[];cash=[];source_hashes={}
    chosen_signatures=set()
    for scenario in registration['scenarios']:
        name,cfg=scenario['name'],scenario['config']
        check(cfg['initial_cash_usd']==1000 and cfg['cash_release_delay_ms']==0,'unexpected shared cash/release config')
        for filename in ('audit.json','attempts.jsonl.gz','cash_ledger.csv'):
            relative=f'{name}/{filename}';actual=digest(run/relative)
            check(actual==completion['output_sha256'][relative],'completed file hash differs: '+relative)
            source_hashes[relative]=actual
        audit=read(run/name/'audit.json')
        check(audit['fixed_priority']==priority,'audit priority differs')
        check(audit['plugin_code_sha256']==frozen['source_plugin_sha256'],'executed plugin differs from registered source')
        chosen=audit['chosen_signals'];suppressed=audit['suppressed_signals'];first=audit['component_first_signals']
        check(len({(x['candidate_id'],x['market_id']) for x in first})==len(first),'duplicate component first signal')
        check(len({x['market_id'] for x in chosen})==len(chosen),'duplicate chosen market')
        check(len(first)==len(chosen)+len(suppressed),'first/chosen/suppressed do not reconcile')
        earliest={}
        for signal in first:
            earliest.setdefault(signal['market_id'],signal)
        chosen_by_market={r['market_id']:r for r in chosen}
        for row in chosen:
            check(all(row[k]==earliest[row['market_id']][k] for k in ('candidate_id','decision_ms','side')),'chosen is not earliest eligible input-order signal')
            simultaneous=row['simultaneous_signals']
            check([rank[r['candidate_id']] for r in simultaneous]==sorted(rank[r['candidate_id']] for r in simultaneous),'simultaneous priority order differs')
            check(simultaneous[0]['candidate_id']==row['selected_candidate'],'wrong priority tie winner')
            check(row['opposite_simultaneous']==(len({r['side'] for r in simultaneous})>1),'opposite tie flag differs')
        first_keys={(r['candidate_id'],r['market_id'],r['decision_ms'],r['side']) for r in first}
        accounted={(r['candidate_id'],r['market_id'],r['decision_ms'],r['side']) for r in chosen}
        for row in suppressed:
            k=row['candidate_id'],row['market_id'],row['decision_ms'],row['side']
            check(k in first_keys and k not in accounted,'suppressed first signal not unique')
            accounted.add(k);winner=chosen_by_market[row['market_id']]
            check(row['selected_candidate']==winner['selected_candidate'] and row['decision_ms']>=winner['decision_ms'],'suppression winner/time differs')
            check(row['reason'] in ('fixed_priority_tie','market_already_consumed_no_retry'),'unknown suppression reason')
            if row['reason']=='fixed_priority_tie':
                check(row['decision_ms']==winner['decision_ms'] and {'candidate_id':row['candidate_id'],'side':row['side']} in winner['simultaneous_signals'],'invalid simultaneous suppression')
        check(accounted==first_keys,'unaccounted component first signal')
        chosen_signatures.add(hashlib.sha256(json.dumps(chosen,sort_keys=True).encode()).hexdigest())
        with gzip.open(run/name/'attempts.jsonl.gz','rt') as f:
            attempts=[json.loads(line) for line in f]
        by_attempt={r['attempt_id']:r for r in attempts}
        check(len(by_attempt)==len(attempts),'duplicate attempt id')
        check(Counter((r['market_id'],r['latency_ms']) for r in attempts)==Counter((market,latency) for market in chosen_by_market for latency in (500,600,1000)),'one attempt per chosen market/latency violated')
        for row in attempts:
            chosen_row=chosen_by_market[row['market_id']]
            check(row['selected_candidate']==chosen_row['selected_candidate'] and row['side']==chosen_row['side'] and row['decision_ms']==chosen_row['decision_ms'],'attempt differs from chosen signal')
            check(row['match_ms']==row['decision_ms']+row['latency_ms'],'match latency differs')
        arbitration.append({'scenario':name,'requested_shares':cfg['shares'],
            'component_first_signals':len(first),'chosen_markets':len(chosen),'suppressed_component_first_signals':len(suppressed),
            'chosen_by_component':dict(Counter(r['selected_candidate'] for r in chosen)),
            'suppression_reasons':dict(Counter(r['reason'] for r in suppressed)),
            'opposite_simultaneous_chosen_markets':sum(r['opposite_simultaneous'] for r in chosen),
            'component_feature_audit':audit['component_feature_audit'],'pair_overlap':audit['pair_overlap'],
            'all_chosen_have_exactly_one_attempt_per_latency':True,'failure_does_not_allow_retry':True})
        states={latency:{'cash':1000.,'reserved':0.,'realized':0.,'last_ms':-1,'last_sequence':-1,
            'cumulative_purchase_cost_usd':0.,'credited_settlement_payout_usd':0.,'positive_payout_events':0,
            'reservations_after_first_positive_payout':0,'event_counts':Counter(),'min_available_cash_usd':1000.}
            for latency in (500,600,1000)}
        for row in read_csv(run/name/'cash_ledger.csv'):
            if row.get('report_cash_basis')!='engine_recorded_event':continue
            state=states[row['latency_ms']];attempt=by_attempt[str(row['attempt_id'])];kind=row['event_type']
            check(row['event_ms']>=state['last_ms'] and row['event_sequence']>state['last_sequence'],'cash sequence regression')
            expected_cash,expected_reserved,expected_pnl=state['cash'],state['reserved'],state['realized']
            if kind=='reservation':
                expected_reserved+=attempt['reserved_usd_at_decision']
                state['reservations_after_first_positive_payout']+=state['positive_payout_events']>0
            elif kind=='reservation_release':expected_reserved=max(0.,expected_reserved-attempt['reserved_usd_at_decision'])
            elif kind in ('fill','no_fill'):
                expected_cash-=attempt['cost_usd'];state['cumulative_purchase_cost_usd']+=attempt['cost_usd']
            elif kind=='settlement':
                expected_cash+=attempt['payout_usd'];expected_pnl+=attempt['net_pnl_usd']
                state['credited_settlement_payout_usd']+=attempt['payout_usd']
                state['positive_payout_events']+=attempt['payout_usd']>0
                check(row['event_ms']==attempt['settlement_ms']==attempt['cash_release_due_ms'],'settlement release timing differs')
            else:raise ValueError('unexpected zero-delay cash event: '+kind)
            check(close(row['cash_usd'],expected_cash) and close(row['reserved_usd'],expected_reserved) and close(row['realized_pnl_usd'],expected_pnl),'cash transition differs')
            available=row['cash_usd']-row['reserved_usd']
            check(available>=-1e-7 and close(row['locked_payout_usd'],0.),'cash insolvency/locked payout mismatch')
            state.update(cash=row['cash_usd'],reserved=row['reserved_usd'],realized=row['realized_pnl_usd'],last_ms=row['event_ms'],last_sequence=row['event_sequence'])
            state['min_available_cash_usd']=min(state['min_available_cash_usd'],available);state['event_counts'][kind]+=1
        for latency,state in states.items():
            subset=[r for r in attempts if r['latency_ms']==latency]
            reported=next(r for r in summary['results'] if r['scenario']==name and r['latency_ms']==latency)
            check(close(state['cash'],reported['ending_cash_usd']) and close(state['min_available_cash_usd'],reported['min_available_cash_usd']),'ending/min cash differs from verified summary')
            check(close(state['cash'],1000-state['cumulative_purchase_cost_usd']+state['credited_settlement_payout_usd']),'cash conservation differs')
            cash.append({'scenario':name,'requested_shares':cfg['shares'],'latency_ms':latency,**state,
                'attempt_status_counts':dict(Counter(r['status'] for r in subset)),
                'actual_shared_attempts_by_selected_component':dict(Counter(r['selected_candidate'] for r in subset)),
                'actual_shared_fills_by_selected_component':dict(Counter(r['selected_candidate'] for r in subset if r['filled_shares']>0)),
                'failed_first_attempts':sum(not r['filled_shares'] for r in subset),
                'unresolved_positions':sum(r['filled_shares']>0 and not r['settled'] for r in subset)})
    check(len(chosen_signatures)==1,'size scenarios selected different component signals')
    for source in summary['results']:
        row=dict(source)
        days=[d for d in daily if all(d[k]==row[k] for k in ('candidate_id','latency_ms','scenario')) and d['market_start_utc_date'] in prediction_dates]
        row['program_cash_path_uncertainty_including_warmup']=row['day_cluster_uncertainty']
        seed=int(hashlib.sha256(f"{row['scenario']}:{row['latency_ms']}:shared_prediction_days".encode()).hexdigest()[:16],16)
        row['day_cluster_uncertainty']=day_bootstrap(days,samples=2000,seed=seed)
        row['day_cluster_uncertainty_basis']='Only Oct8/9 prediction days; Oct7 warmup excluded from model CI but retained in coverage and cash-path evidence.'
        results.append(row)
    check(len(results)==9,'expected one portfolio with 3 sizes by 3 latencies')
    value={'schema':'shared-portfolio-completed-assessment-v1','new_portfolio_configurations':1,'new_candidate_rules':0,
        'completed_execution_scenarios':9,'new_backtests_by_diagnostic':0,'formal_oos_result':None,'qualified_strategies':0,
        'selection_bias':frozen['selection_bias'],'fixed_priority':priority,'arbitration':frozen['arbitration'],
        'input_projection_preflight':'passed','completion_sha256':digest(run/'completion.json'),
        'input_registration_sha256':digest(portfolio_registration),'parity_report_sha256':digest(parity_report),
        'prediction_coverage_sha256':digest(prediction_coverage),'source_summary_sha256':digest(report/'summary.json'),
        'verified_completed_output_sha256':source_hashes,'code_sha256':digest(__file__),
        'reconciliation_differences':[],'arbitration_diagnostics':arbitration,'cash_transition_diagnostics':cash,'results':results,
        'capital_scope':'One $1000 shared account per alternative size/latency. Never add the nine accounts or individual component PnL.',
        'cash_release_limit':'Zero delay after locally known official outcome is an assumed simulation redemption rule, not observed payout execution. Later reservations share the credited cash balance.',
        'limitations':['Partial consumed-development recordings; no full seven-day or OOS claim.','Two partial prediction dates and post-result component choice preclude statistical edge confirmation.','Displayed depth is a simulated capacity upper bound; realized-only drawdown excludes mark-to-market inventory risk.']}
    (report/'shared_assessment.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for field in ('run','report','portfolio-registration','prediction-coverage','parity-report'):
        p.add_argument('--'+field,required=True)
    a=p.parse_args();r=assess(a.run,a.report,a.portfolio_registration,a.prediction_coverage,a.parity_report)
    print(json.dumps({'output':str(Path(a.report)/'shared_assessment.json'),'completed_execution_scenarios':r['completed_execution_scenarios'],'reconciliation_differences':r['reconciliation_differences'],'new_backtests':0}))
